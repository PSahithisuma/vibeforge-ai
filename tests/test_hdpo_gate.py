from __future__ import annotations

import os
import pytest
from agents.training.hdpo_dataset_generator import HDPODatasetGenerator, DPOPreferencePair


class TestHDPODataset:
    def test_generate_pairs_creates_valid_dataset(self):
        generator = HDPODatasetGenerator()
        pairs = generator.generate_pairs(count_per_sample=2)
        assert len(pairs) == 8
        assert all(isinstance(p, DPOPreferencePair) for p in pairs)

    def test_chosen_response_prioritizes_local_routing(self):
        generator = HDPODatasetGenerator()
        pairs = generator.generate_pairs(count_per_sample=1)
        for p in pairs:
            assert "ROUTE_LOCAL" in p.chosen
            assert "ROUTE_ESCALATE" in p.rejected
            assert p.cost_saved_usd > 0

    def test_export_jsonl_writes_valid_json(self, tmp_path):
        generator = HDPODatasetGenerator()
        pairs = generator.generate_pairs(count_per_sample=2)
        out_file = str(tmp_path / "dataset.jsonl")
        generator.export_jsonl(out_file, pairs)

        assert os.path.exists(out_file)
        lines = open(out_file, encoding="utf-8").readlines()
        assert len(lines) == 8
