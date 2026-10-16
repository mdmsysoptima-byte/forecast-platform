"""Model registry. Scans models/ and loads each manifest into model_registry."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.models import ModelRegistry

log = get_logger(__name__)
_CACHE: dict[str, object] = {}


def discover_packages(root: Path | None = None) -> list[dict]:
    root = root or settings.model_packages_dir
    found: list[dict] = []
    if not root.exists():
        return found
    for pkg in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        manifest = pkg / "manifest.yaml"
        if not manifest.exists():
            continue
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        data["package_path"] = str(pkg)
        found.append(data)
    return found


def sync_registry(db: Session, root: Path | None = None) -> list[ModelRegistry]:
    """Upsert every discovered manifest. No code change needed to add a model."""
    out: list[ModelRegistry] = []
    for m in discover_packages(root):
        row = db.execute(select(ModelRegistry).where(ModelRegistry.code == m["code"])).scalar_one_or_none()
        if row is None:
            row = ModelRegistry(code=m["code"])
            db.add(row)
        row.name = m.get("name", m["code"])
        row.version = str(m.get("version", "1.0.0"))
        row.package_path = m["package_path"]
        row.supported_grains = m.get("supported_grains", [])
        row.supported_levels = m.get("supported_levels", {})
        row.required_drivers = m.get("required_drivers", [])
        row.optional_drivers = m.get("optional_drivers", [])
        row.output_has_interval = bool(m.get("output_has_interval", False))
        row.description = (m.get("description") or "").strip()
        out.append(row)
    db.flush()
    log.info("model registry güncellendi", extra={"entity": "model_registry"})
    return out


def load_model(entry: ModelRegistry):
    """Dynamically import the model package and instantiate the model class."""
    if entry.code in _CACHE:
        return _CACHE[entry.code]
    pkg = Path(entry.package_path)
    manifest = yaml.safe_load((pkg / "manifest.yaml").read_text(encoding="utf-8"))
    module_file = pkg / manifest.get("entry", "model.py")
    class_name = manifest["class"]
    mod_name = f"forecast_model_{entry.code}"
    spec = importlib.util.spec_from_file_location(mod_name, module_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Model paketi yüklenemedi: {entry.code}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    instance = getattr(module, class_name)()
    _CACHE[entry.code] = instance
    return instance


def nearest_supported_level(entry: ModelRegistry, requested: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Fall back to the closest level the model supports, and say so."""
    supported = entry.supported_levels or {}
    resolved: dict[str, str] = {}
    notes: list[str] = []
    for dim, lvl in (requested or {}).items():
        options = supported.get(dim)
        if not options:
            resolved[dim] = lvl
            continue
        if lvl in options:
            resolved[dim] = lvl
        else:
            resolved[dim] = options[0]
            notes.append(
                f"{dim} boyutunda {lvl} seviyesi model tarafindan desteklenmiyor,"
                f" {options[0]} seviyesinde çalıştırıldı ve uzlaştırıldı"
            )
    return resolved, notes
