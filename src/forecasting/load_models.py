from __future__ import annotations

import json
import hashlib
import logging
import pickle
from dataclasses import dataclass
from typing import Any
from pathlib import Path

from models.inference import format_champion_version, predict_timing_week_probabilities, predict_value_floor_m3

logger = logging.getLogger(__name__)

LFS_POINTER_HEADER = "version https://git-lfs.github.com/spec/v1"


def _looks_like_lfs_pointer(text: str) -> bool:
    return text.startswith(LFS_POINTER_HEADER)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class HorizonForecast:
    floor: float
    ceiling: float
    floor_time: str
    ceiling_time: str
    breach_prob: float
    expected_return: float
    expected_range: float


@dataclass(frozen=True)
class M3Forecast:
    floor_m3: float
    floor_week_m3: int
    floor_week_m3_confidence: float
    floor_week_m3_top3: list[dict]
    expected_return_m3: float
    expected_range_m3: float


class ChampionModelSet:
    """Champion forecaster with d1/w1/q1 compatibility and m3 extension."""

    def __init__(self, model_registry_dir: Path | None = None) -> None:
        self._registry = model_registry_dir or Path("data/training/models")
        self._models_file_registry = self._registry.parent / "models_file"
        self._load_diagnostics: dict[str, str] = {}
        logger.info(
            "[forecasting] champion load preflight cwd=%s registry=%s registry_exists=%s models_file_registry=%s models_file_exists=%s",
            Path.cwd(),
            self._registry,
            self._registry.exists(),
            self._models_file_registry,
            self._models_file_registry.exists(),
        )
        self._d1_champion = self._load_artifact("d1")
        self._w1_champion = self._load_artifact("w1")
        self._q1_champion = self._load_artifact("q1")
        self._value_champion = self._load_artifact("value")
        self._timing_champion = self._load_artifact("timing")
        base_version = format_champion_version(self._value_champion, self._timing_champion)
        self.version = f"d1:{self._artifact_version(self._d1_champion)}|w1:{self._artifact_version(self._w1_champion)}|q1:{self._artifact_version(self._q1_champion)}|{base_version}"
        logger.info(
            "[forecasting] loaded champions d1=%s w1=%s q1=%s value=%s timing=%s version=%s diagnostics=%s",
            "ok" if self._d1_champion is not None else "missing",
            "ok" if self._w1_champion is not None else "missing",
            "ok" if self._q1_champion is not None else "missing",
            "ok" if self._value_champion is not None else "missing",
            "ok" if self._timing_champion is not None else "missing",
            self.version,
            self._load_diagnostics,
        )

    @property
    def is_available(self) -> bool:
        """Only publish forecasts when both trained artifacts are available."""
        return all(artifact is not None for artifact in (self._d1_champion, self._w1_champion, self._q1_champion, self._value_champion, self._timing_champion))

    @property
    def load_diagnostics(self) -> dict[str, str]:
        return dict(self._load_diagnostics)

    @property
    def model_readout(self) -> dict[str, dict[str, str]]:
        artifacts = {
            "d1": self._d1_champion,
            "w1": self._w1_champion,
            "q1": self._q1_champion,
            "value": self._value_champion,
            "timing": self._timing_champion,
        }
        return {
            task: {
                "status": "loaded" if artifact is not None else "missing",
                "version": self._artifact_version(artifact),
                "diagnostic": self._load_diagnostics.get(task, "unknown"),
            }
            for task, artifact in artifacts.items()
        }

    @staticmethod
    def _load_json(path: Path) -> Any | None:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        if _looks_like_lfs_pointer(text):
            logger.error(
                "[forecasting] json artifact is a Git LFS pointer (real model payload missing locally) path=%s",
                path,
            )
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("[forecasting] invalid json artifact path=%s", path)
            return None

    @staticmethod
    def _load_pickle(path: Path) -> Any | None:
        if not path.exists():
            return None
        head = path.read_bytes()[:200]
        try:
            head_text = head.decode("utf-8")
        except UnicodeDecodeError:
            head_text = ""
        if _looks_like_lfs_pointer(head_text):
            logger.error(
                "[forecasting] pickle artifact is a Git LFS pointer (real model payload missing locally) path=%s",
                path,
            )
            return None
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning("[forecasting] failed to read pickle artifact path=%s error=%s", path, exc)
            return None

    @staticmethod
    def _load_manifest(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            logger.warning("[forecasting] invalid manifest json path=%s", path)
            return None

    def _validate_manifest(self, task: str, pkl_path: Path, manifest: dict | None) -> bool:
        if manifest is None:
            logger.warning("[forecasting] missing manifest task=%s pkl=%s", task, pkl_path)
            return False

        expected_task = str(manifest.get("task", "")).strip()
        if expected_task != task:
            logger.warning("[forecasting] manifest task mismatch expected=%s actual=%s path=%s", task, expected_task, pkl_path)
            return False

        expected_hash = str(manifest.get("sha256", "")).strip().lower()
        if not expected_hash:
            logger.warning("[forecasting] manifest without sha256 task=%s path=%s", task, pkl_path)
            return False

        current_hash = _sha256_file(pkl_path).lower()
        if expected_hash != current_hash:
            logger.warning(
                "[forecasting] manifest hash mismatch task=%s path=%s expected_sha256=%s current_sha256=%s",
                task,
                pkl_path,
                expected_hash,
                current_hash,
            )
            return False

        return True

    @staticmethod
    def _artifact_version(artifact: Any | None) -> str:
        if isinstance(artifact, dict):
            raw = artifact.get("version") or artifact.get("model_name") or "unknown"
            return str(raw)
        return "unknown"

    @staticmethod
    def _horizon_params(artifact: Any | None, defaults: dict[str, Any]) -> dict[str, Any]:
        params = artifact.get("params", {}) if isinstance(artifact, dict) else {}
        out = dict(defaults)
        for key, value in params.items():
            if key in out:
                out[key] = value
        return out

    def _load_artifact(self, task: str) -> Any | None:
        pkl_path = self._models_file_registry / f"{task}_champion.pkl"
        manifest_path = self._models_file_registry / f"{task}_champion.manifest.json"
        json_path = self._registry / f"{task}_champion.json"

        logger.info(
            "[forecasting] trying champion task=%s json=%s json_exists=%s pkl=%s pkl_exists=%s manifest=%s manifest_exists=%s",
            task,
            json_path,
            json_path.exists(),
            pkl_path,
            pkl_path.exists(),
            manifest_path,
            manifest_path.exists(),
        )

        artifact = self._load_json(json_path)
        if artifact is not None:
            logger.info(
                "[forecasting] using json champion task=%s path=%s payload_type=%s",
                task,
                json_path,
                type(artifact).__name__,
            )
            self._load_diagnostics[task] = f"loaded:json:{json_path}:type={type(artifact).__name__}"
            return artifact

        if pkl_path.exists():
            manifest = self._load_manifest(manifest_path)
            if self._validate_manifest(task, pkl_path, manifest):
                artifact = self._load_pickle(pkl_path)
                if artifact is not None:
                    logger.info(
                        "[forecasting] using pkl champion task=%s path=%s manifest=%s payload_type=%s",
                        task,
                        pkl_path,
                        manifest_path,
                        type(artifact).__name__,
                    )
                    self._load_diagnostics[task] = f"loaded:pkl:{pkl_path}:type={type(artifact).__name__}"
                    return artifact
                logger.warning("[forecasting] pkl champion unreadable after manifest validation task=%s path=%s", task, pkl_path)
                self._load_diagnostics[task] = f"invalid_pkl:{pkl_path}"
            else:
                self._load_diagnostics[task] = f"invalid_manifest:{manifest_path}"

        if json_path.exists():
            text = json_path.read_text(encoding="utf-8")
            if _looks_like_lfs_pointer(text):
                reason = f"lfs_pointer:{json_path}"
            else:
                reason = f"invalid_json:{json_path}"
        elif task not in self._load_diagnostics:
            reason = f"missing_all_artifacts:json={json_path};pkl={pkl_path}"
        else:
            reason = self._load_diagnostics[task]
        self._load_diagnostics[task] = reason
        logger.warning(
            "[forecasting] champion artifact missing task=%s json=%s pkl=%s manifest=%s reason=%s",
            task,
            json_path,
            pkl_path,
            manifest_path,
            reason,
        )
        return None

    def _base(self, row: dict) -> tuple[float, float, float]:
        close = float(row["close"])
        atr = float(row.get("atr_14") or max(0.5, close * 0.01))
        vol_score = float(row.get("vol_regime_score") or 1.0)
        return close, atr, vol_score

    @staticmethod
    def _classic_family(model_name: str) -> str:
        name = str(model_name or "").lower()
        if name.startswith("evt_cp_"):
            return "evt_cp"
        if name.startswith("xgboost_"):
            return "xgboost"
        if name.startswith("lstm_"):
            return "lstm"
        if name.startswith("qenet_"):
            return "qenet"
        return ""

    def _predict_classic_horizon(self, row: dict, artifact: Any | None, horizon: str) -> HorizonForecast | None:
        if not isinstance(artifact, dict):
            return None
        family = self._classic_family(str(artifact.get("model_name", "")))
        if not family:
            return None

        close, _atr, _vol = self._base(row)
        floor_delta = float(artifact.get("floor_delta") or 0.01)
        ceiling_delta = float(artifact.get("ceiling_delta") or 0.01)
        floor = close * (1.0 - floor_delta)
        ceiling = close * (1.0 + ceiling_delta)
        expected_return = ((floor + ceiling) / 2.0 - close) / max(close, 1e-6)
        spread = max(0.01, ceiling - floor)

        d1_times = {
            "evt_cp": ("OPEN_PLUS_2H", "CLOSE"),
            "xgboost": ("OPEN_PLUS_4H", "OPEN_PLUS_6H"),
            "lstm": ("OPEN_PLUS_2H", "OPEN_PLUS_6H"),
            "qenet": ("OPEN_PLUS_4H", "CLOSE"),
        }
        w1_times = {
            "evt_cp": ("2", "5"),
            "xgboost": ("3", "4"),
            "lstm": ("1", "5"),
            "qenet": ("2", "4"),
        }
        q1_times = {
            "evt_cp": ("15", "45"),
            "xgboost": ("20", "40"),
            "lstm": ("10", "45"),
            "qenet": ("15", "35"),
        }
        horizon_times = {"d1": d1_times, "w1": w1_times, "q1": q1_times}
        floor_time, ceiling_time = horizon_times[horizon].get(family, ("", ""))

        metrics = artifact.get("metrics", {}) if isinstance(artifact.get("metrics"), dict) else {}
        spread_mae = float(metrics.get("mae_spread") or spread / max(close, 1.0))
        breach_prob = min(0.98, max(0.05, 0.2 + spread_mae / max(close, 1.0)))
        return HorizonForecast(
            floor=round(floor, 4),
            ceiling=round(ceiling, 4),
            floor_time=floor_time,
            ceiling_time=ceiling_time,
            breach_prob=round(breach_prob, 4),
            expected_return=round(expected_return, 6),
            expected_range=round(spread, 4),
        )

    def predict_d1(self, row: dict) -> HorizonForecast:
        classic = self._predict_classic_horizon(row, self._d1_champion, horizon="d1")
        if classic is not None:
            return classic
        close, atr, vol = self._base(row)
        ai_bias = float(row.get("ai_consensus_score") or 0.0)
        cfg = self._horizon_params(
            self._d1_champion,
            {
                "move_base": 1.2,
                "move_vol_mult": 0.4,
                "bias_mult": 0.15,
                "breach_base": 0.35,
                "breach_vol_mult": 0.15,
                "expected_feature": "rel_strength_20",
                "expected_mult": 0.5,
                "time_floor_positive": 2.0,
                "time_floor_negative": 4.0,
                "time_ceiling_positive": 6.0,
                "time_ceiling_negative": 8.0,
            },
        )
        move = atr * (float(cfg["move_base"]) + float(cfg["move_vol_mult"]) * vol)
        floor = close - move * (1.0 - float(cfg["bias_mult"]) * ai_bias)
        ceiling = close + move * (1.0 + float(cfg["bias_mult"]) * ai_bias)
        feature_value = float(row.get(str(cfg["expected_feature"])) or 0.0)
        floor_slot = int(float(cfg["time_floor_positive"]) if feature_value > 0 else float(cfg["time_floor_negative"]))
        ceiling_slot = int(float(cfg["time_ceiling_positive"]) if feature_value > 0 else float(cfg["time_ceiling_negative"]))
        floor_time_map = {2: "OPEN_PLUS_2H", 4: "OPEN_PLUS_4H"}
        ceil_time_map = {6: "OPEN_PLUS_6H", 8: "CLOSE"}
        return HorizonForecast(
            floor=round(floor, 4),
            ceiling=round(ceiling, 4),
            floor_time=floor_time_map.get(floor_slot, "OPEN_PLUS_2H"),
            ceiling_time=ceil_time_map.get(ceiling_slot, "CLOSE"),
            breach_prob=round(min(0.95, float(cfg["breach_base"]) + float(cfg["breach_vol_mult"]) * vol), 4),
            expected_return=round((ai_bias * 0.02) + (feature_value * float(cfg["expected_mult"])), 6),
            expected_range=round(max(0.01, ceiling - floor), 4),
        )

    def predict_w1(self, row: dict) -> HorizonForecast:
        classic = self._predict_classic_horizon(row, self._w1_champion, horizon="w1")
        if classic is not None:
            return classic
        close, atr, vol = self._base(row)
        cfg = self._horizon_params(
            self._w1_champion,
            {
                "move_base": 2.2,
                "move_vol_mult": 0.5,
                "bias_mult": 0.1,
                "breach_base": 0.42,
                "breach_vol_mult": 0.18,
                "expected_feature": "rel_strength_20",
                "expected_mult": 0.8,
                "time_floor_positive": 2.0,
                "time_floor_negative": 1.0,
                "time_ceiling_positive": 5.0,
                "time_ceiling_negative": 4.0,
            },
        )
        feat = float(row.get(str(cfg["expected_feature"])) or 0.0)
        move = atr * (float(cfg["move_base"]) + float(cfg["move_vol_mult"]) * vol)
        return HorizonForecast(
            floor=round(close - move * (1.0 - float(cfg["bias_mult"]) * feat), 4),
            ceiling=round(close + move * (1.0 + float(cfg["bias_mult"]) * feat), 4),
            floor_time=str(int(float(cfg["time_floor_positive"]) if feat > 0 else float(cfg["time_floor_negative"]))),
            ceiling_time=str(int(float(cfg["time_ceiling_positive"]) if feat > 0 else float(cfg["time_ceiling_negative"]))),
            breach_prob=round(min(0.97, float(cfg["breach_base"]) + float(cfg["breach_vol_mult"]) * vol), 4),
            expected_return=round(feat * float(cfg["expected_mult"]), 6),
            expected_range=round(move * 2, 4),
        )

    def predict_q1(self, row: dict) -> HorizonForecast:
        classic = self._predict_classic_horizon(row, self._q1_champion, horizon="q1")
        if classic is not None:
            return classic
        close, atr, vol = self._base(row)
        cfg = self._horizon_params(
            self._q1_champion,
            {
                "move_base": 3.6,
                "move_vol_mult": 0.6,
                "bias_mult": 0.1,
                "breach_base": 0.5,
                "breach_vol_mult": 0.15,
                "expected_feature": "momentum_20",
                "expected_mult": 0.9,
                "time_floor_positive": 3.0,
                "time_floor_negative": 2.0,
                "time_ceiling_positive": 10.0,
                "time_ceiling_negative": 8.0,
            },
        )
        feat = float(row.get(str(cfg["expected_feature"])) or 0.0)
        move = atr * (float(cfg["move_base"]) + float(cfg["move_vol_mult"]) * vol)
        return HorizonForecast(
            floor=round(close - move * (1.0 - float(cfg["bias_mult"]) * feat), 4),
            ceiling=round(close + move * (1.0 + float(cfg["bias_mult"]) * feat), 4),
            floor_time=str(int(float(cfg["time_floor_positive"]) if feat > 0 else float(cfg["time_floor_negative"]))),
            ceiling_time=str(int(float(cfg["time_ceiling_positive"]) if feat > 0 else float(cfg["time_ceiling_negative"]))),
            breach_prob=round(min(0.98, float(cfg["breach_base"]) + float(cfg["breach_vol_mult"]) * vol), 4),
            expected_return=round(feat * float(cfg["expected_mult"]), 6),
            expected_range=round(move * 2, 4),
        )

    def predict_m3(self, row: dict) -> M3Forecast | None:
        required = ["close", "atr_14", "trend_context_m3", "drawdown_13w"]
        if any(row.get(key) in (None, "") for key in required):
            return None

        close = float(row["close"])
        atr = float(row.get("atr_14") or max(0.5, close * 0.01))
        trend = float(row.get("trend_context_m3") or 0.0)
        dd = float(row.get("drawdown_13w") or 0.0)
        align = float(row.get("ai_horizon_alignment") or 0.0)

        floor = predict_value_floor_m3(row, self._value_champion)
        probs = predict_timing_week_probabilities(row, self._timing_champion)

        best_idx = max(range(13), key=lambda idx: probs[idx])
        top3_idx = sorted(range(13), key=lambda idx: probs[idx], reverse=True)[:3]
        top3 = [{"week": idx + 1, "probability": round(probs[idx], 6)} for idx in top3_idx]

        expected_return = round(0.5 * trend + 0.2 * align - 0.15 * abs(dd), 6)
        expected_range = round(max(0.01, atr * (10 + 2 * (1 + abs(dd)))), 4)

        return M3Forecast(
            floor_m3=round(floor, 4),
            floor_week_m3=best_idx + 1,
            floor_week_m3_confidence=round(probs[best_idx], 6),
            floor_week_m3_top3=top3,
            expected_return_m3=expected_return,
            expected_range_m3=expected_range,
        )


def load_champion_models(model_registry_dir: Path | None = None) -> ChampionModelSet:
    return ChampionModelSet(model_registry_dir=model_registry_dir)
