import yaml
from pathlib import Path


class AnalysisConfig:

    def __init__(self, config_path: str):
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Brak pliku config: {config_path}")

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

    def get_camera_config(self, camera: str):

        default = self.config.get("default", {})
        camera_cfg = self.config.get("cameras", {}).get(camera, {})

        merged = default.copy()

        for key, value in camera_cfg.items():
            if isinstance(value, dict):
                base = merged.get(key, {}).copy()
                base.update(value)
                merged[key] = base
            else:
                merged[key] = value

        return merged