from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class SBOMGenerator:
    """
    Generates CycloneDX 1.5 JSON SBOMs by analyzing dependency manifests
    in assembled project files (pom.xml, requirements.txt, package.json).
    """

    def generate(
        self,
        assembled_files: dict[str, str],
        stack_profile: str = "java_spring",
        app_name: str = "vibeforge-generated-app",
        version: str = "1.0.0",
    ) -> dict[str, Any]:
        """Produce CycloneDX 1.5 SBOM dictionary from assembled files."""
        components = self._extract_components(assembled_files, stack_profile)

        sbom = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "serialNumber": f"urn:uuid:{uuid4()}",
            "version": 1,
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tools": [
                    {
                        "vendor": "VibeForge",
                        "name": "SBOM-Generator",
                        "version": "1.0.0",
                    }
                ],
                "component": {
                    "type": "application",
                    "name": app_name,
                    "version": version,
                },
            },
            "components": components,
            "dependencies": [
                {
                    "ref": f"pkg:generic/{app_name}@{version}",
                    "dependsOn": [c["bom-ref"] for c in components if "bom-ref" in c],
                }
            ],
        }
        return sbom

    def generate_json(
        self,
        assembled_files: dict[str, str],
        stack_profile: str = "java_spring",
        app_name: str = "vibeforge-generated-app",
        version: str = "1.0.0",
    ) -> str:
        """Produce formatted JSON string."""
        return json.dumps(
            self.generate(assembled_files, stack_profile, app_name, version),
            indent=2,
        )

    def _extract_components(
        self,
        files: dict[str, str],
        stack_profile: str,
    ) -> list[dict[str, Any]]:
        components: list[dict[str, Any]] = []

        # 1. Java / Maven (pom.xml)
        for path, content in files.items():
            if path.endswith("pom.xml") or "pom.xml" in path:
                components.extend(self._parse_pom_xml(content))

        # 2. Python (requirements.txt / pyproject.toml)
        for path, content in files.items():
            if path.endswith("requirements.txt"):
                components.extend(self._parse_requirements_txt(content))
            elif path.endswith("pyproject.toml"):
                components.extend(self._parse_pyproject_toml(content))

        # 3. Node.js (package.json)
        for path, content in files.items():
            if path.endswith("package.json"):
                components.extend(self._parse_package_json(content))

        # Fallback if no components detected
        if not components:
            components.append({
                "type": "library",
                "name": f"base-{stack_profile}-runtime",
                "version": "latest",
                "purl": f"pkg:generic/base-{stack_profile}-runtime@latest",
                "bom-ref": f"pkg:generic/base-{stack_profile}-runtime@latest",
            })

        return _dedup_components(components)

    def _parse_pom_xml(self, content: str) -> list[dict[str, Any]]:
        components = []
        dep_blocks = re.findall(r"<dependency>(.*?)</dependency>", content, re.DOTALL)
        for block in dep_blocks:
            g_match = re.search(r"<groupId>(.*?)</groupId>", block)
            a_match = re.search(r"<artifactId>(.*?)</artifactId>", block)
            v_match = re.search(r"<version>(.*?)</version>", block)
            if g_match and a_match:
                group = g_match.group(1).strip()
                artifact = a_match.group(1).strip()
                version = v_match.group(1).strip() if v_match else "unknown"
                purl = f"pkg:maven/{group}/{artifact}@{version}"
                components.append({
                    "type": "library",
                    "group": group,
                    "name": artifact,
                    "version": version,
                    "purl": purl,
                    "bom-ref": purl,
                })
        return components

    def _parse_requirements_txt(self, content: str) -> list[dict[str, Any]]:
        components = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # match pkg==1.0.0 or pkg>=1.0.0 or pkg
            m = re.match(r"^([a-zA-Z0-9_\-\.]+)(?:[>=<~!]=?([a-zA-Z0-9_\-\.]+))?", line)
            if m:
                name = m.group(1)
                version = m.group(2) or "latest"
                purl = f"pkg:pypi/{name}@{version}"
                components.append({
                    "type": "library",
                    "name": name,
                    "version": version,
                    "purl": purl,
                    "bom-ref": purl,
                })
        return components

    def _parse_pyproject_toml(self, content: str) -> list[dict[str, Any]]:
        components = []
        in_deps = False
        for line in content.splitlines():
            line = line.strip()
            if "dependencies" in line and "=" in line:
                in_deps = True
                continue
            if in_deps:
                if line.startswith("]"):
                    in_deps = False
                    continue
                clean = line.strip('",\' ')
                if clean:
                    m = re.match(r"^([a-zA-Z0-9_\-\.]+)(?:[>=<~!]=?([a-zA-Z0-9_\-\.]+))?", clean)
                    if m:
                        name = m.group(1)
                        version = m.group(2) or "latest"
                        purl = f"pkg:pypi/{name}@{version}"
                        components.append({
                            "type": "library",
                            "name": name,
                            "version": version,
                            "purl": purl,
                            "bom-ref": purl,
                        })
        return components

    def _parse_package_json(self, content: str) -> list[dict[str, Any]]:
        components = []
        try:
            data = json.loads(content)
            all_deps = {}
            all_deps.update(data.get("dependencies", {}))
            all_deps.update(data.get("devDependencies", {}))
            for name, ver in all_deps.items():
                ver_clean = str(ver).lstrip("^~>=<")
                purl = f"pkg:npm/{name}@{ver_clean}"
                components.append({
                    "type": "library",
                    "name": name,
                    "version": ver_clean,
                    "purl": purl,
                    "bom-ref": purl,
                })
        except (json.JSONDecodeError, TypeError):
            pass
        return components


def _dedup_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for c in components:
        ref = c.get("bom-ref") or c.get("name")
        if ref not in seen:
            seen.add(ref)
            result.append(c)
    return result