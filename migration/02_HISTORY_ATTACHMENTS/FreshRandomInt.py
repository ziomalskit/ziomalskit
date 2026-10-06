import secrets

MAX_SEED = 1125899906842624

class FreshRandomInt:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "min_value": ("INT", {"default": 0, "min": 0, "max": MAX_SEED}),
                "max_value": ("INT", {"default": MAX_SEED, "min": 0, "max": MAX_SEED}),
            }
        }

    RETURN_TYPES = ("INT",)
    RETURN_NAMES = ("INT",)
    FUNCTION = "generate"
    CATEGORY = "utils/seed"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Force this node to execute for every queued prompt instead of being cached.
        return float("nan")

    def generate(self, min_value, max_value):
        if min_value > max_value:
            min_value, max_value = max_value, min_value
        value = min_value + secrets.randbelow(max_value - min_value + 1)
        return (value,)

NODE_CLASS_MAPPINGS = {
    "FreshRandomInt": FreshRandomInt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FreshRandomInt": "Fresh Random Int",
}
