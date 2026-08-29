from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.data import load_pair_csv
from app.market_data.models import MarketBar
from app.models import MarketFrame
from app.workspace import default_workspace_root

from .diff import compare_datasets
from .families import DatasetFamilyRepository
from .models import (
    DataQualityReport,
    DatasetDefinition,
    DatasetFamily,
    DatasetFamilyHistory,
    DatasetImportRequest,
    DatasetPreview,
    DatasetProvenance,
    DatasetRevisionDiff,
)

CANONICAL_FIELDS = ("timestamp", "symbol", "open", "high", "low", "close", "volume")
REQUIRED_FIELDS = ("timestamp", "symbol", "close")
ALIASES = {
    "timestamp": ("timestamp", "datetime", "date", "time"),
    "symbol": ("symbol", "ticker", "asset", "instrument"),
    "close": ("close", "adj_close", "adjusted_close", "price", "last"),
    "open": ("open",),
    "high": ("high",),
    "low": ("low",),
    "volume": ("volume", "vol"),
}


@dataclass(frozen=True, slots=True)
class _PreviewContent:
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class _NormalizedRow:
    original_index: int
    timestamp: datetime
    symbol: str
    fields: dict[str, float]


class DatasetValidationError(ValueError):
    pass


class DatasetRegistry:
    def __init__(self, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = (
            default_workspace_root()
            if workspace_root is None
            else Path(workspace_root).expanduser().resolve()
        )
        self.datasets_root = self.workspace_root / ".vqd" / "datasets"
        self.family_repository = DatasetFamilyRepository(self.workspace_root)
        self._previews: dict[str, _PreviewContent] = {}
        self._migrate_legacy_datasets()

    @staticmethod
    def _write_metadata(path: Path, definition: DatasetDefinition) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(definition.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _migrate_legacy_datasets(self) -> None:
        if not self.datasets_root.exists():
            return
        records = [
            (
                metadata_path,
                DatasetDefinition.model_validate_json(metadata_path.read_text(encoding="utf-8")),
            )
            for metadata_path in sorted(self.datasets_root.glob("*/metadata.json"))
        ]
        known_families = {
            family.latest_dataset_id: family for family in self.family_repository.list()
        }
        migrated_records: list[tuple[Path, DatasetDefinition]] = []
        for metadata_path, definition in records:
            if definition.dataset_family_id is None:
                family = known_families.get(definition.dataset_id)
                if family is None:
                    family = DatasetFamily(
                        dataset_family_id=self.family_repository.new_id(),
                        name=definition.name,
                        created_at=definition.created_at,
                        latest_dataset_id=definition.dataset_id,
                        revision_count=1,
                    )
                    self.family_repository.create(family)
                    known_families[definition.dataset_id] = family
                definition = definition.model_copy(
                    update={
                        "dataset_family_id": family.dataset_family_id,
                        "revision": 1,
                        "parent_dataset_id": None,
                        "revision_reason": definition.revision_reason
                        or "Legacy dataset assigned to a version family",
                    }
                )
                self._write_metadata(metadata_path, definition)
            migrated_records.append((metadata_path, definition))

        revisions_by_family: dict[str, list[DatasetDefinition]] = defaultdict(list)
        for _, definition in migrated_records:
            if definition.dataset_family_id is not None:
                revisions_by_family[definition.dataset_family_id].append(definition)
        for family_id, revisions in revisions_by_family.items():
            revisions.sort(key=lambda item: (item.revision, item.created_at, item.dataset_id))
            latest = revisions[-1]
            family = self.family_repository.get(family_id)
            if family is None:
                self.family_repository.create(
                    DatasetFamily(
                        dataset_family_id=family_id,
                        name=revisions[0].name,
                        created_at=min(item.created_at for item in revisions),
                        latest_dataset_id=latest.dataset_id,
                        revision_count=len(revisions),
                    )
                )
            elif family.latest_dataset_id != latest.dataset_id or family.revision_count != len(
                revisions
            ):
                self.family_repository.save(
                    family.model_copy(
                        update={
                            "latest_dataset_id": latest.dataset_id,
                            "revision_count": len(revisions),
                        }
                    )
                )

    def _family_context(
        self, dataset_family_id: str | None
    ) -> tuple[str, int, str | None, DatasetFamily | None]:
        if dataset_family_id is None:
            return self.family_repository.new_id(), 1, None, None
        if dataset_family_id == "dataset-family-pairs-sample":
            raise DatasetValidationError(
                "The built-in sample Dataset Family is immutable and cannot accept revisions"
            )
        try:
            family = self.family_repository.get(dataset_family_id)
        except ValueError as exc:
            raise DatasetValidationError(str(exc)) from exc
        if family is None:
            raise DatasetValidationError(
                f"Dataset family '{dataset_family_id}' was not found; select an existing family"
            )
        revisions = self.revisions(dataset_family_id)
        if not revisions:
            raise DatasetValidationError(
                f"Dataset family '{dataset_family_id}' has no registered revisions"
            )
        latest = revisions[-1]
        return family.dataset_family_id, latest.revision + 1, latest.dataset_id, family

    def _finish_family_write(
        self, definition: DatasetDefinition, previous: DatasetFamily | None
    ) -> None:
        if definition.dataset_family_id is None:
            raise DatasetValidationError("Dataset revision is missing a family id")
        if previous is None:
            self.family_repository.create(
                DatasetFamily(
                    dataset_family_id=definition.dataset_family_id,
                    name=definition.name,
                    created_at=definition.created_at,
                    latest_dataset_id=definition.dataset_id,
                    revision_count=1,
                )
            )
            return
        self.family_repository.save(
            previous.model_copy(
                update={
                    "latest_dataset_id": definition.dataset_id,
                    "revision_count": definition.revision,
                }
            )
        )

    @staticmethod
    def _read_csv(content: bytes) -> tuple[tuple[str, ...], list[dict[str, str]]]:
        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DatasetValidationError("CSV must be UTF-8 encoded") from exc
        reader = csv.DictReader(io.StringIO(decoded))
        if not reader.fieldnames:
            raise DatasetValidationError("CSV must contain a header row")
        columns = tuple(reader.fieldnames)
        if len(set(columns)) != len(columns):
            raise DatasetValidationError("CSV column names must be unique")
        rows = [dict(row) for row in reader]
        if not rows:
            raise DatasetValidationError("CSV must contain at least one data row")
        return columns, rows

    @staticmethod
    def _candidate_mapping(columns: tuple[str, ...]) -> dict[str, str]:
        lowered = {column.strip().lower(): column for column in columns}
        return {
            canonical: lowered[alias]
            for canonical, aliases in ALIASES.items()
            for alias in aliases
            if alias in lowered
        }

    @staticmethod
    def _detected_types(columns: tuple[str, ...], rows: list[dict[str, str]]) -> dict[str, str]:
        detected: dict[str, str] = {}
        for column in columns:
            values = [row[column].strip() for row in rows[:100] if row[column].strip()]
            if values and all(_is_float(value) for value in values):
                detected[column] = "number"
            elif values and all(_is_datetime(value) for value in values):
                detected[column] = "datetime"
            else:
                detected[column] = "string"
        return detected

    def preview(self, filename: str, content: bytes) -> DatasetPreview:
        columns, rows = self._read_csv(content)
        candidate = self._candidate_mapping(columns)
        detected_timezone = None
        timestamp_column = candidate.get("timestamp")
        if timestamp_column:
            parsed = _parse_datetime(rows[0][timestamp_column], 2)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                detected_timezone = str(parsed.tzinfo)
        fingerprint = hashlib.sha256(content).hexdigest()
        preview_id = f"preview-{fingerprint[:20]}"
        self._previews[preview_id] = _PreviewContent(filename, content)
        return DatasetPreview(
            preview_id=preview_id,
            filename=filename,
            columns=columns,
            rows=tuple(rows[:8]),
            detected_types=self._detected_types(columns, rows),
            detected_timezone=detected_timezone,
            candidate_mapping=candidate,
        )

    @staticmethod
    def _timezone(value: str | None) -> ZoneInfo | None:
        if value is None:
            return None
        try:
            return ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise DatasetValidationError(f"Unknown timezone '{value}'") from exc

    def _normalize(
        self, content: bytes, request: DatasetImportRequest
    ) -> tuple[list[_NormalizedRow], DataQualityReport, tuple[str, ...], str]:
        columns, raw_rows = self._read_csv(content)
        missing_mapping = [field for field in REQUIRED_FIELDS if field not in request.mapping]
        if missing_mapping:
            raise DatasetValidationError(
                f"Column mapping is missing required fields: {', '.join(missing_mapping)}"
            )
        unknown_sources = sorted(set(request.mapping.values()) - set(columns))
        if unknown_sources:
            raise DatasetValidationError(
                f"Mapped source columns do not exist: {', '.join(unknown_sources)}"
            )
        unsupported = sorted(set(request.mapping) - set(CANONICAL_FIELDS))
        if unsupported:
            raise DatasetValidationError(f"Unsupported canonical fields: {', '.join(unsupported)}")
        source_timezone = self._timezone(request.timezone)
        normalized: list[_NormalizedRow] = []
        missing_close = 0
        for index, row in enumerate(raw_rows):
            line = index + 2
            raw_timestamp = row[request.mapping["timestamp"]].strip()
            raw_symbol = row[request.mapping["symbol"]].strip()
            raw_close = row[request.mapping["close"]].strip()
            if not raw_close:
                missing_close += 1
                continue
            if not raw_symbol:
                raise DatasetValidationError(f"Missing symbol at CSV line {line}")
            timestamp = _parse_datetime(raw_timestamp, line)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                if source_timezone is None:
                    raise DatasetValidationError(
                        "Naive timestamps require an explicit IANA timezone before import"
                    )
                timestamp = timestamp.replace(tzinfo=source_timezone)
            fields: dict[str, float] = {}
            for canonical in ("open", "high", "low", "close", "volume"):
                source = request.mapping.get(canonical)
                if source is None or not row[source].strip():
                    continue
                try:
                    fields[canonical] = float(row[source])
                except ValueError as exc:
                    raise DatasetValidationError(
                        f"Invalid {canonical} value at CSV line {line}"
                    ) from exc
            if fields["close"] <= 0:
                raise DatasetValidationError(f"close must be positive at CSV line {line}")
            normalized.append(_NormalizedRow(index, timestamp.astimezone(UTC), raw_symbol, fields))
        if missing_close:
            raise DatasetValidationError(
                f"close is missing in {missing_close} row(s); imports never forward-fill prices"
            )
        keys = [(item.symbol, item.timestamp) for item in normalized]
        duplicates = sum(count - 1 for count in Counter(keys).values() if count > 1)
        if duplicates:
            raise DatasetValidationError(
                f"Duplicate (symbol, timestamp) bars detected: {duplicates}; import rejected"
            )
        sorted_rows = sorted(normalized, key=lambda item: (item.timestamp, item.symbol))
        rows_reordered = sum(
            item.original_index != position for position, item in enumerate(sorted_rows)
        )
        symbols = tuple(sorted({item.symbol for item in sorted_rows}))
        timestamps_by_symbol: dict[str, set[datetime]] = defaultdict(set)
        for item in sorted_rows:
            timestamps_by_symbol[item.symbol].add(item.timestamp)
        union = set().union(*timestamps_by_symbol.values())
        intersection = set.intersection(*timestamps_by_symbol.values())
        alignment_gaps = len(union) * len(symbols) - sum(
            len(values) for values in timestamps_by_symbol.values()
        )
        issues: list[str] = []
        if rows_reordered:
            issues.append(f"ROWS_REORDERED: {rows_reordered} row positions changed")
        if alignment_gaps:
            issues.append(
                f"DATA_ALIGNMENT_GAP: {alignment_gaps} symbol/timestamp cells are missing; "
                "strict synchronized runs use only the intersection"
            )
        timezone_label = request.timezone or "embedded offset"
        quality = DataQualityReport(
            status="WARNING" if issues else "VALID",
            rows=len(sorted_rows),
            symbols=len(symbols),
            start=sorted_rows[0].timestamp,
            end=sorted_rows[-1].timestamp,
            duplicates=0,
            missing_required_values=0,
            rows_reordered=rows_reordered,
            alignment_gaps=alignment_gaps,
            timezone=timezone_label,
            issues=tuple(issues),
        )
        canonical_fields = tuple(
            field
            for field in ("open", "high", "low", "close", "volume")
            if any(field in item.fields for item in sorted_rows)
        )
        return sorted_rows, quality, canonical_fields, str(len(intersection))

    @staticmethod
    def _semantic_content(rows: list[_NormalizedRow]) -> str:
        semantic = [
            {
                "timestamp": row.timestamp.isoformat(),
                "symbol": row.symbol,
                **{key: repr(value) for key, value in sorted(row.fields.items())},
            }
            for row in rows
        ]
        return json.dumps(semantic, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _infer_frequency(rows: list[_NormalizedRow]) -> str:
        grouped: dict[str, list[datetime]] = defaultdict(list)
        for row in rows:
            grouped[row.symbol].append(row.timestamp)
        deltas = [
            int((current - previous).total_seconds())
            for timestamps in grouped.values()
            for previous, current in zip(timestamps, timestamps[1:], strict=False)
            if current > previous
        ]
        if not deltas:
            return "unknown"
        seconds = int(statistics.median(deltas))
        return "1D" if seconds == 86_400 else f"{seconds}s"

    def commit(self, request: DatasetImportRequest) -> DatasetDefinition:
        preview = self._previews.get(request.preview_id)
        if preview is None:
            raise DatasetValidationError(
                f"Preview '{request.preview_id}' was not found; upload the CSV again"
            )
        rows, quality, fields, synchronized_count = self._normalize(preview.content, request)
        semantic = self._semantic_content(rows)
        fingerprint = f"sha256:{hashlib.sha256(semantic.encode()).hexdigest()}"
        dataset_id = f"dataset-{fingerprint.removeprefix('sha256:')[:16]}"
        existing = self.get(dataset_id)
        if existing is not None:
            if (
                request.dataset_family_id is not None
                and existing.dataset_family_id != request.dataset_family_id
            ):
                raise DatasetValidationError(
                    "This exact content is already registered in another Dataset Family; "
                    "an immutable revision cannot belong to two families"
                )
            return existing
        family_id, revision, parent_dataset_id, family = self._family_context(
            request.dataset_family_id
        )
        definition = DatasetDefinition(
            dataset_id=dataset_id,
            name=family.name if family is not None else request.name.strip() or preview.filename,
            source_type="CSV",
            timezone="UTC",
            frequency=request.frequency or self._infer_frequency(rows),
            symbols=tuple(sorted({row.symbol for row in rows})),
            fields=fields,
            row_count=len(rows),
            synchronized_bar_count=int(synchronized_count),
            start_time=rows[0].timestamp,
            end_time=rows[-1].timestamp,
            created_at=datetime.now(UTC),
            content_fingerprint=fingerprint,
            dataset_family_id=family_id,
            revision=revision,
            parent_dataset_id=parent_dataset_id,
            revision_reason=(request.revision_reason.strip() if request.revision_reason else None),
            source_timezone=request.timezone or "embedded offset",
            column_mapping=dict(request.mapping),
            quality=quality,
        )
        self._write_dataset(definition, rows)
        self._finish_family_write(definition, family)
        return definition

    def _write_dataset(self, definition: DatasetDefinition, rows: list[_NormalizedRow]) -> None:
        target = self.datasets_root / definition.dataset_id
        target.mkdir(parents=True, exist_ok=False)
        data_path = target / "data.csv"
        fields = definition.fields
        with data_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("timestamp", "symbol", *fields))
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "timestamp": row.timestamp.isoformat().replace("+00:00", "Z"),
                        "symbol": row.symbol,
                        **{field: row.fields.get(field, "") for field in fields},
                    }
                )
        self._write_metadata(target / "metadata.json", definition)

    def commit_provider_bars(
        self,
        *,
        name: str,
        bars: tuple[MarketBar, ...],
        provenance: DatasetProvenance,
        security_names: dict[str, str] | None = None,
        dataset_family_id: str | None = None,
        revision_reason: str | None = None,
    ) -> DatasetDefinition:
        if not bars:
            raise DatasetValidationError("The provider returned no bars for this request")
        rows = [
            _NormalizedRow(
                original_index=index,
                timestamp=bar.event_time.astimezone(UTC),
                symbol=bar.symbol,
                fields={
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                },
            )
            for index, bar in enumerate(bars)
        ]
        keys = [(row.symbol, row.timestamp) for row in rows]
        duplicates = sum(count - 1 for count in Counter(keys).values() if count > 1)
        if duplicates:
            raise DatasetValidationError(
                f"Provider returned {duplicates} duplicate symbol/timestamp bar(s)"
            )
        rows.sort(key=lambda row: (row.timestamp, row.symbol))
        symbols = tuple(sorted({row.symbol for row in rows}))
        timestamp_sets = {
            symbol: {row.timestamp for row in rows if row.symbol == symbol} for symbol in symbols
        }
        intersection = set.intersection(*timestamp_sets.values())
        union = set.union(*timestamp_sets.values())
        alignment_gaps = len(union) * len(symbols) - len(rows)
        issues = (
            (
                (
                    f"DATA_ALIGNMENT_GAP: {alignment_gaps} symbol/timestamp cells are missing; "
                    "strict synchronized runs use only the intersection"
                ),
            )
            if alignment_gaps
            else ()
        )
        quality = DataQualityReport(
            status="WARNING" if issues else "VALID",
            rows=len(rows),
            symbols=len(symbols),
            start=rows[0].timestamp,
            end=rows[-1].timestamp,
            duplicates=0,
            missing_required_values=0,
            rows_reordered=0,
            alignment_gaps=alignment_gaps,
            timezone="UTC",
            issues=issues,
        )
        semantic = self._semantic_content(rows)
        fingerprint = f"sha256:{hashlib.sha256(semantic.encode()).hexdigest()}"
        dataset_id = f"dataset-{fingerprint.removeprefix('sha256:')[:16]}"
        existing = self.get(dataset_id)
        if existing is not None:
            if dataset_family_id is not None and existing.dataset_family_id != dataset_family_id:
                raise DatasetValidationError(
                    "This exact provider content is already registered in another Dataset Family"
                )
            return existing
        family_id, revision, parent_dataset_id, family = self._family_context(dataset_family_id)
        definition = DatasetDefinition(
            dataset_id=dataset_id,
            name=(
                family.name
                if family is not None
                else name.strip() or f"{' + '.join(symbols)} · {bars[0].timeframe}"
            ),
            source_type="PROVIDER",
            timezone="UTC",
            frequency=bars[0].timeframe,
            symbols=symbols,
            fields=("open", "high", "low", "close", "volume"),
            row_count=len(rows),
            synchronized_bar_count=len(intersection),
            start_time=rows[0].timestamp,
            end_time=rows[-1].timestamp,
            created_at=datetime.now(UTC),
            content_fingerprint=fingerprint,
            dataset_family_id=family_id,
            revision=revision,
            parent_dataset_id=parent_dataset_id,
            revision_reason=revision_reason.strip() if revision_reason else None,
            source_timezone="UTC",
            column_mapping={field: field for field in CANONICAL_FIELDS},
            quality=quality,
            provenance=provenance,
            security_names=security_names or {},
        )
        self._write_dataset(definition, rows)
        self._finish_family_write(definition, family)
        return definition

    def _user_definitions(self) -> tuple[DatasetDefinition, ...]:
        if not self.datasets_root.exists():
            return ()
        definitions: list[DatasetDefinition] = []
        for metadata_path in sorted(self.datasets_root.glob("*/metadata.json")):
            definitions.append(
                DatasetDefinition.model_validate_json(metadata_path.read_text(encoding="utf-8"))
            )
        return tuple(definitions)

    def list(self) -> tuple[DatasetDefinition, ...]:
        return (self._built_in_definition(), *self._user_definitions())

    def get(self, dataset_id: str) -> DatasetDefinition | None:
        if dataset_id == "pairs-sample-v1":
            return self._built_in_definition()
        metadata = self.datasets_root / dataset_id / "metadata.json"
        return (
            DatasetDefinition.model_validate_json(metadata.read_text(encoding="utf-8"))
            if metadata.exists()
            else None
        )

    def _built_in_frames(self) -> tuple[MarketFrame, ...]:
        source = Path(__file__).parents[3] / "sample_data" / "pairs_daily.csv"
        return tuple(bar.as_frame() for bar in load_pair_csv(source))

    def _built_in_definition(self) -> DatasetDefinition:
        frames = self._built_in_frames()
        semantic = json.dumps(
            [
                [frame.timestamp.isoformat(), frame.value("ASSET_A"), frame.value("ASSET_B")]
                for frame in frames
            ],
            separators=(",", ":"),
        )
        fingerprint = f"sha256:{hashlib.sha256(semantic.encode()).hexdigest()}"
        quality = DataQualityReport(
            status="VALID",
            rows=len(frames) * 2,
            symbols=2,
            start=frames[0].timestamp,
            end=frames[-1].timestamp,
            duplicates=0,
            missing_required_values=0,
            rows_reordered=0,
            alignment_gaps=0,
            timezone="UTC",
        )
        return DatasetDefinition(
            dataset_id="pairs-sample-v1",
            name="Pairs Daily Sample",
            source_type="BUILT_IN",
            timezone="UTC",
            frequency="1D",
            symbols=("ASSET_A", "ASSET_B"),
            fields=("close",),
            row_count=len(frames) * 2,
            synchronized_bar_count=len(frames),
            start_time=frames[0].timestamp,
            end_time=frames[-1].timestamp,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            content_fingerprint=fingerprint,
            dataset_family_id="dataset-family-pairs-sample",
            revision=1,
            source_timezone="UTC",
            column_mapping={
                "timestamp": "timestamp",
                "symbol": "asset columns",
                "close": "asset_a_close / asset_b_close",
            },
            quality=quality,
        )

    def families(self) -> tuple[DatasetFamily, ...]:
        self._migrate_legacy_datasets()
        built_in = DatasetFamily(
            dataset_family_id="dataset-family-pairs-sample",
            name="Pairs Daily Sample",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            latest_dataset_id="pairs-sample-v1",
            revision_count=1,
        )
        return (built_in, *self.family_repository.list())

    def get_family(self, dataset_family_id: str) -> DatasetFamily | None:
        if dataset_family_id == "dataset-family-pairs-sample":
            return self.families()[0]
        self._migrate_legacy_datasets()
        return self.family_repository.get(dataset_family_id)

    def revisions(self, dataset_family_id: str) -> tuple[DatasetDefinition, ...]:
        if dataset_family_id == "dataset-family-pairs-sample":
            return (self._built_in_definition(),)
        self._migrate_legacy_datasets()
        revisions = [
            definition
            for definition in self._user_definitions()
            if definition.dataset_family_id == dataset_family_id
        ]
        return tuple(
            sorted(revisions, key=lambda item: (item.revision, item.created_at, item.dataset_id))
        )

    def family_history(self, dataset_family_id: str) -> DatasetFamilyHistory | None:
        family = self.get_family(dataset_family_id)
        if family is None:
            return None
        return DatasetFamilyHistory(family=family, revisions=self.revisions(dataset_family_id))

    def newer_revision(self, dataset_id: str) -> DatasetDefinition | None:
        definition = self.get(dataset_id)
        if definition is None or definition.dataset_family_id is None:
            return None
        family = self.get_family(definition.dataset_family_id)
        if family is None or family.latest_dataset_id == dataset_id:
            return None
        return self.get(family.latest_dataset_id)

    def compare(self, left_dataset_id: str, right_dataset_id: str) -> DatasetRevisionDiff:
        left = self.get(left_dataset_id)
        right = self.get(right_dataset_id)
        if left is None:
            raise KeyError(f"Dataset '{left_dataset_id}' was not found")
        if right is None:
            raise KeyError(f"Dataset '{right_dataset_id}' was not found")
        return compare_datasets(left, right)

    def load_frames(
        self,
        dataset_id: str,
        required_symbols: tuple[str, ...] = (),
        *,
        allow_partial: bool = False,
    ) -> tuple[MarketFrame, ...]:
        if dataset_id == "pairs-sample-v1":
            frames = self._built_in_frames()
        else:
            definition = self.get(dataset_id)
            if definition is None:
                raise KeyError(f"Dataset '{dataset_id}' was not found")
            grouped: dict[datetime, dict[str, dict[str, float]]] = defaultdict(dict)
            path = self.datasets_root / dataset_id / "data.csv"
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                    fields = {
                        field: float(row[field])
                        for field in definition.fields
                        if row.get(field, "") != ""
                    }
                    grouped[timestamp][row["symbol"]] = fields
            frames = tuple(
                MarketFrame(timestamp=timestamp, values=values)
                for timestamp, values in sorted(grouped.items())
            )
        symbols = required_symbols or tuple(
            sorted(set.intersection(*(set(frame.symbols) for frame in frames)))
        )
        if allow_partial:
            return tuple(
                MarketFrame(
                    timestamp=frame.timestamp,
                    values={
                        symbol: frame.values[symbol] for symbol in symbols if symbol in frame.values
                    },
                    available_at=frame.available_at,
                )
                for frame in frames
                if any(symbol in frame.values for symbol in symbols)
            )
        return tuple(
            MarketFrame(
                timestamp=frame.timestamp,
                values={symbol: frame.values[symbol] for symbol in symbols},
                available_at=frame.available_at,
            )
            for frame in frames
            if all(symbol in frame.values for symbol in symbols)
        )


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _parse_datetime(value: str, line: int) -> datetime:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise DatasetValidationError(f"Invalid timestamp at CSV line {line}: {value!r}") from exc


def _is_datetime(value: str) -> bool:
    try:
        _parse_datetime(value, 0)
        return True
    except DatasetValidationError:
        return False


dataset_registry = DatasetRegistry()
