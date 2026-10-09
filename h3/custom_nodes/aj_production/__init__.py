"""AJ-owned lifecycle dependencies. No downloads, inference or Vast actions."""
import uuid

from aiohttp import web
import comfy.model_management
import nodes
from server import PromptServer

SERVICE_EPOCH = uuid.uuid4().hex


@PromptServer.instance.routes.get("/aj/service_epoch")
async def service_epoch(_request):
    return web.json_response({"service_epoch": SERVICE_EPOCH})


def release_managed_models():
    # This affects only this ComfyUI process. The separate Prompt Worker has its
    # own model lifecycle. Conditioning tensors are deliberately retained.
    comfy.model_management.unload_all_models()
    comfy.model_management.soft_empty_cache()


class AJAnalysisText:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"text": ("STRING", {"multiline": True})}}
    RETURN_TYPES = ("STRING",)
    FUNCTION = "value"
    CATEGORY = "AJ/production"
    def value(self, text):
        return (text,)


class AJConditioningBoundary:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"first": ("CONDITIONING",), "second": ("CONDITIONING",),
                             "release_encoder": ("BOOLEAN", {"default": True})}}
    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "INT")
    FUNCTION = "release"
    CATEGORY = "AJ/production"
    @classmethod
    def IS_CHANGED(cls, **_kwargs):
        # A cached conditioning result must still release a previously resident
        # render model before the next profile's transformer can load.
        return float("nan")
    def release(self, first, second, release_encoder):
        if release_encoder:
            release_managed_models()
        return (first, second, 1)


class AJLateUNETLoader(nodes.UNETLoader):
    @classmethod
    def INPUT_TYPES(cls):
        result = super().INPUT_TYPES()
        result["required"]["after_conditioning"] = ("INT", {"forceInput": True})
        return result
    FUNCTION = "load_after_conditioning"
    CATEGORY = "AJ/production"
    def load_after_conditioning(self, unet_name, weight_dtype, after_conditioning):
        if after_conditioning != 1:
            raise ValueError("conditioning release boundary was not completed")
        return super().load_unet(unet_name, weight_dtype)


class AJVideoBoundary:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"video": ("VIDEO",), "release_models": ("BOOLEAN", {"default": True})}}
    RETURN_TYPES = ("VIDEO",)
    FUNCTION = "release"
    CATEGORY = "AJ/production"
    @classmethod
    def IS_CHANGED(cls, **_kwargs):
        return float("nan")
    def release(self, video, release_models):
        if release_models:
            release_managed_models()
        return (video,)


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    AJAnalysisText, AJConditioningBoundary, AJLateUNETLoader, AJVideoBoundary)}
