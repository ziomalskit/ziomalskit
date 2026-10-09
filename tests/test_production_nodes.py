"""Exercise AJ node contracts with CPU stubs, never import real CUDA modules."""
import asyncio
import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1] / "h3"


class ProductionNodeTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        memory = types.ModuleType("comfy.model_management")
        memory.unload_all_models = lambda: self.events.append("unload")
        memory.soft_empty_cache = lambda: self.events.append("empty")
        comfy = types.ModuleType("comfy")
        comfy.model_management = memory
        nodes = types.ModuleType("nodes")
        events = self.events
        class Loader:
            @classmethod
            def INPUT_TYPES(cls):
                return {"required": {"unet_name": (["CPUFixture"],), "weight_dtype": (["default"],)}}
            def load_unet(self, unet_name, weight_dtype):
                events.append(("load", unet_name, weight_dtype))
                return ("CPU model sentinel",)
        nodes.UNETLoader = Loader
        self.routes = {}
        class Routes:
            def get(_self, path):
                def register(function):
                    self.routes[path] = function
                    return function
                return register
        server = types.ModuleType("server")
        server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes()))
        aiohttp = types.ModuleType("aiohttp")
        aiohttp.web = types.SimpleNamespace(json_response=lambda data: data)
        modules = {"comfy": comfy, "comfy.model_management": memory, "nodes": nodes, "server": server, "aiohttp": aiohttp}
        self.context = patch.dict(sys.modules, modules)
        self.context.start()
        self.addCleanup(self.context.stop)
        spec = importlib.util.spec_from_file_location("_aj_cpu_node_" + uuid.uuid4().hex, ROOT / "custom_nodes/aj_production/__init__.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_conditioning_boundary_releases_before_late_transformer_load(self):
        first, second = object(), object()
        result = self.module.AJConditioningBoundary().release(first, second, True)
        self.assertEqual(result, (first, second, 1))
        self.assertEqual(self.events, ["unload", "empty"])
        self.module.AJLateUNETLoader().load_after_conditioning("CPUFixture", "default", result[2])
        self.assertEqual(self.events[-1], ("load", "CPUFixture", "default"))
        self.assertTrue(math.isnan(self.module.AJConditioningBoundary.IS_CHANGED()))
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.module.AJLateUNETLoader().load_after_conditioning("CPUFixture", "default", 0)
        self.assertEqual(len(self.events), 3)

    def test_video_boundary_releases_once_and_retains_output_object(self):
        video = object()
        self.assertEqual(self.module.AJVideoBoundary().release(video, True), (video,))
        self.assertEqual(self.events, ["unload", "empty"])
        self.assertTrue(math.isnan(self.module.AJVideoBoundary.IS_CHANGED()))
        self.module.AJVideoBoundary().release(video, False)
        self.assertEqual(len(self.events), 2)

    def test_analysis_text_and_epoch_endpoint_have_no_model_or_mutation_side_effect(self):
        text = "exact durable Step 0–2 analysis"
        self.assertEqual(self.module.AJAnalysisText().value(text), (text,))
        before = self.module.SERVICE_EPOCH
        self.assertEqual(asyncio.run(self.routes["/aj/service_epoch"](None)), {"service_epoch": before})
        self.assertEqual(self.module.SERVICE_EPOCH, before)
        self.assertEqual(len(before), 32)
        self.assertEqual(self.events, [])
