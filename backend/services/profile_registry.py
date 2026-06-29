import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class ReportConfig:
    name: str
    required_entities: list[str] = field(default_factory=list)
    kpis: list[str] = field(default_factory=list)
    charts: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    summary: str = ""

_registry: dict[str, ReportConfig] = {}

def load_profiles() -> None:
    _registry.clear()
    config_dir = Path(__file__).parent / "report_profiles" / "configs"
    if not config_dir.exists():
        return
        
    for file_path in config_dir.glob("*.yaml"):
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if not data or "name" not in data:
                continue
            config = ReportConfig(
                name=data["name"],
                required_entities=data.get("required_entities", []),
                kpis=data.get("kpis", []),
                charts=data.get("charts", []),
                insights=data.get("insights", []),
                summary=data.get("summary", "")
            )
            _registry[config.name] = config

def get_profile(report_type: str) -> ReportConfig | None:
    if not _registry:
        load_profiles()
    return _registry.get(report_type)

def get_all_profiles() -> list[ReportConfig]:
    if not _registry:
        load_profiles()
    return list(_registry.values())
