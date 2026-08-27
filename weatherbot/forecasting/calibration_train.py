"""Deterministic calibration training and untouched-holdout reporting."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from weatherbot.forecasting.calibration import CalibrationError
from weatherbot.forecasting.calibration_fit import CalibrationFitResult, fit_calibration_artifact
from weatherbot.forecasting.calibration_io import load_calibration_dataset

_REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CalibrationTrainingOutput:
    fit: CalibrationFitResult
    report: dict[str, object]


def train_calibration_dataset(
    *,
    records_path: str | Path,
    manifest_path: str | Path,
    model_version: str,
    created_at_utc: datetime,
    training_end: date,
    validation_start: date,
    validation_end: date,
    min_sample_count: int,
) -> CalibrationTrainingOutput:
    dataset = load_calibration_dataset(records_path, manifest_path)
    manifest = dataset.manifest
    model_version = model_version.strip()
    if not model_version:
        raise CalibrationError("model_version must not be blank")
    created = _aware_utc(created_at_utc, label="artifact creation time")
    if isinstance(min_sample_count, bool):
        raise CalibrationError("min_sample_count must be an integer")
    if min_sample_count < 2:
        raise CalibrationError("min_sample_count must be at least two")
    if training_end < manifest.start_date:
        raise CalibrationError("training_end precedes the dataset start")
    if validation_start != training_end + timedelta(days=1):
        raise CalibrationError("validation_start must be the calendar day after training_end")
    if validation_end != manifest.end_date:
        raise CalibrationError("validation_end must equal the dataset end date")
    if validation_start > validation_end:
        raise CalibrationError("validation interval is empty or reversed")

    fit = fit_calibration_artifact(
        dataset.samples,
        model_version=model_version,
        created_at_utc=created,
        forecast_contract_id=manifest.forecast_contract_id,
        observation_contract_id=manifest.observation_contract_id,
        training_start=manifest.start_date,
        training_end=training_end,
        validation_start=validation_start,
        validation_end=validation_end,
        dataset_sha256=manifest.dataset_sha256,
        min_sample_count=min_sample_count,
    )
    metrics = fit.validation
    report: dict[str, object] = {
        "schema_version": _REPORT_SCHEMA_VERSION,
        "model_version": fit.artifact.model_version,
        "artifact_sha256": fit.artifact.artifact_sha256,
        "dataset_sha256": manifest.dataset_sha256,
        "dataset_manifest_sha256": manifest.manifest_sha256,
        "forecast_contract_id": manifest.forecast_contract_id,
        "observation_contract_id": manifest.observation_contract_id,
        "training_start": manifest.start_date.isoformat(),
        "training_end": training_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "validation_end": validation_end.isoformat(),
        "min_sample_count": min_sample_count,
        "fitted_group_count": len(fit.artifact.groups),
        "validation": metrics.to_mapping(),
        "baseline_comparison": {
            "fixed_sigma_f": 2.0,
            "calibrated_minus_baseline_mean_log_score": (
                metrics.mean_log_score - metrics.baseline_mean_log_score
            ),
            "calibrated_minus_baseline_mean_ranked_probability_score": (
                metrics.mean_ranked_probability_score
                - metrics.baseline_mean_ranked_probability_score
            ),
            "calibrated_better_mean_log_score": (
                metrics.mean_log_score < metrics.baseline_mean_log_score
            ),
            "calibrated_better_mean_ranked_probability_score": (
                metrics.mean_ranked_probability_score
                < metrics.baseline_mean_ranked_probability_score
            ),
        },
    }
    return CalibrationTrainingOutput(fit=fit, report=report)


def write_training_output(
    output: CalibrationTrainingOutput,
    *,
    artifact_path: str | Path,
    report_path: str | Path,
) -> None:
    artifact_file = Path(artifact_path)
    report_file = Path(report_path)
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(artifact_file, output.fit.artifact.to_json())
    _atomic_write_text(
        report_file,
        json.dumps(output.report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _aware_utc(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalibrationError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    try:
        return _aware_utc(parsed, label="timestamp")
    except CalibrationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise CalibrationError(f"stale output temporary file exists: {temporary}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fit a versioned temperature calibration artifact from a frozen dataset"
    )
    parser.add_argument("--records", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--created-at-utc", required=True, type=_timestamp)
    parser.add_argument("--training-end", required=True, type=date.fromisoformat)
    parser.add_argument("--validation-start", required=True, type=date.fromisoformat)
    parser.add_argument("--validation-end", required=True, type=date.fromisoformat)
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument(
        "--artifact-out",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--report-out",
        required=True,
        type=Path,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = train_calibration_dataset(
        records_path=args.records,
        manifest_path=args.manifest,
        model_version=args.model_version,
        created_at_utc=args.created_at_utc,
        training_end=args.training_end,
        validation_start=args.validation_start,
        validation_end=args.validation_end,
        min_sample_count=args.min_sample_count,
    )
    write_training_output(
        output,
        artifact_path=args.artifact_out,
        report_path=args.report_out,
    )
    print(
        json.dumps(
            {
                "model_version": output.fit.artifact.model_version,
                "artifact_sha256": output.fit.artifact.artifact_sha256,
                "fitted_group_count": len(output.fit.artifact.groups),
                "validation_sample_count": output.fit.validation.sample_count,
                "calibrated_better_mean_log_score": (
                    output.fit.validation.mean_log_score
                    < output.fit.validation.baseline_mean_log_score
                ),
                "calibrated_better_mean_ranked_probability_score": (
                    output.fit.validation.mean_ranked_probability_score
                    < output.fit.validation.baseline_mean_ranked_probability_score
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
