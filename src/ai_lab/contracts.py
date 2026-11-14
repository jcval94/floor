"""Versioned schemas: only registered recipes can become executable jobs."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


def default_models() -> list[Literal["dummy", "ridge", "extra_trees"]]:
    return ["dummy", "ridge", "extra_trees"]


def default_strategies() -> list[
    Literal["mean_reversion_floor_w1", "breakout_protected_by_floor"]
]:
    return ["mean_reversion_floor_w1", "breakout_protected_by_floor"]


CONTRACT_VERSION = "1.2"
RECIPES = (
    "model_comparison",
    "feature_audit",
    "feature_ablation",
    "frozen_model_comparison",
    "strategy_comparison",
)
MODELS = ("dummy", "ridge", "extra_trees")


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"
    question: str = Field(min_length=5, max_length=2000)
    recipe: Literal[
        "model_comparison",
        "feature_audit",
        "feature_ablation",
        "frozen_model_comparison",
        "strategy_comparison",
    ]
    dataset_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    models: list[Literal["dummy", "ridge", "extra_trees"]] = Field(
        default_factory=default_models,
        min_length=1,
        max_length=3,
    )
    frozen_model_ids: list[str] = Field(
        default_factory=lambda: [
            "w1_champion",
            "w1_challenger_20260925T214133Z",
            "w1_challenger_20260924T192816Z",
        ],
        min_length=2,
        max_length=3,
    )
    strategies: list[
        Literal["mean_reversion_floor_w1", "breakout_protected_by_floor"]
    ] = Field(
        default_factory=default_strategies,
        min_length=1,
        max_length=2,
    )
    features: list[str] = Field(min_length=1, max_length=16)
    drop_features: list[str] = Field(default_factory=list, max_length=15)
    folds: int = Field(default=3, ge=2, le=5)
    seed: int = Field(default=42, ge=0, le=2147483647)
    objective: Literal["mae_floor_pct", "net_return"] = "mae_floor_pct"
    max_seconds: int = Field(default=60, ge=5, le=120)
    max_memory_mb: int = Field(default=3072, ge=1024, le=4096)
    max_cost_usd: float = Field(default=0, ge=0, le=0)
    notification: Literal["slack"] = "slack"


class Result(BaseModel):
    contract_version: str = CONTRACT_VERSION
    ok: bool = True
    data_status: Literal["EXPERIMENTAL", "BLOCKED", "NOT_AVAILABLE"] = "EXPERIMENTAL"
    source: str = "isolated-ai-lab"
    data: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: dict | None = None
