from __future__ import annotations

import json
from agents.graphs.nodes.sbom_generator import SBOMGenerator


class TestSBOMGenerator:
    def _gen(self):
        return SBOMGenerator()

    def test_generates_valid_cyclonedx_format(self):
        sbom = self._gen().generate({})
        assert sbom["bomFormat"] == "CycloneDX"
        assert sbom["specVersion"] == "1.5"
        assert "serialNumber" in sbom
        assert sbom["serialNumber"].startswith("urn:uuid:")
        assert "metadata" in sbom
        assert "components" in sbom
        assert "dependencies" in sbom

    def test_metadata_contains_tools_and_component(self):
        sbom = self._gen().generate({}, app_name="test-app", version="2.0.0")
        meta = sbom["metadata"]
        assert meta["component"]["name"] == "test-app"
        assert meta["component"]["version"] == "2.0.0"
        assert meta["component"]["type"] == "application"
        assert len(meta["tools"]) >= 1

    def test_parses_maven_pom_xml(self):
        pom = """
        <project>
            <dependencies>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-starter-web</artifactId>
                    <version>3.2.0</version>
                </dependency>
                <dependency>
                    <groupId>org.postgresql</groupId>
                    <artifactId>postgresql</artifactId>
                    <version>42.6.0</version>
                </dependency>
            </dependencies>
        </project>
        """
        sbom = self._gen().generate({"pom.xml": pom}, stack_profile="java_spring")
        names = [c["name"] for c in sbom["components"]]
        assert "spring-boot-starter-web" in names
        assert "postgresql" in names
        purls = [c["purl"] for c in sbom["components"]]
        assert "pkg:maven/org.springframework.boot/spring-boot-starter-web@3.2.0" in purls

    def test_parses_python_requirements_txt(self):
        reqs = """
        # Core requirements
        fastapi==0.110.0
        uvicorn>=0.28.0
        pydantic
        asyncpg==0.29.0
        """
        sbom = self._gen().generate({"requirements.txt": reqs}, stack_profile="python_fastapi")
        names = [c["name"] for c in sbom["components"]]
        assert "fastapi" in names
        assert "uvicorn" in names
        assert "pydantic" in names
        assert "asyncpg" in names

    def test_parses_package_json(self):
        pkg = json.dumps({
            "name": "express-app",
            "dependencies": {
                "express": "^4.19.2",
                "pg": "~8.11.3"
            },
            "devDependencies": {
                "typescript": "^5.4.0"
            }
        })
        sbom = self._gen().generate({"package.json": pkg}, stack_profile="node_express")
        names = [c["name"] for c in sbom["components"]]
        assert "express" in names
        assert "pg" in names
        assert "typescript" in names
        purls = [c["purl"] for c in sbom["components"]]
        assert "pkg:npm/express@4.19.2" in purls

    def test_fallback_component_when_no_manifests(self):
        sbom = self._gen().generate({"README.md": "# Hello"}, stack_profile="java_spring")
        assert len(sbom["components"]) == 1
        assert "base-java_spring-runtime" in sbom["components"][0]["name"]

    def test_generate_json_returns_valid_json_string(self):
        json_str = self._gen().generate_json({"requirements.txt": "fastapi==0.110.0"})
        data = json.loads(json_str)
        assert data["bomFormat"] == "CycloneDX"
        assert len(data["components"]) == 1

    def test_deduplicates_identical_components(self):
        pom = """
        <dependency>
            <groupId>org.slf4j</groupId>
            <artifactId>slf4j-api</artifactId>
            <version>2.0.9</version>
        </dependency>
        <dependency>
            <groupId>org.slf4j</groupId>
            <artifactId>slf4j-api</artifactId>
            <version>2.0.9</version>
        </dependency>
        """
        sbom = self._gen().generate({"pom.xml": pom})
        names = [c["name"] for c in sbom["components"]]
        assert names.count("slf4j-api") == 1