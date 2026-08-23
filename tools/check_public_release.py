#!/usr/bin/env python3
"""Non-executing safety checks for Advanced Astronomy public releases.

The checker never rewrites a notebook.  It reports paths and cell indexes, but
does not print cell source, outputs, answer values, or test bodies.

Examples:

    python3 tools/check_public_release.py \
        --release-root ../aa_class \
        aa_x/x_astrodynamics_1 aa_x/x_waves_and_light_1

    python3 tools/check_public_release.py \
        --release-root ../aa_class --allow-review
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


FORBIDDEN_PUBLIC_PATHS = {
    "aa_ss/ss_sol/ss_sol.pdf": "instructor/answer-bearing PDF",
    "aa_ss/ss_solar_system_1/ss_solar_system_1.pdf":
        "instructor/answer-bearing visual-comparison PDF",
}

FORBIDDEN_NAME_RE = re.compile(
    r"(?:^|[_\-. ])(?:test|tests|solution|solutions|answer[_ -]?key|"
    r"instructor)(?:[_\-. ]|$)",
    re.IGNORECASE,
)

CONTENT_RULES = (
    (
        "legacy_answer_region",
        re.compile(r"\b(?:BEGIN|END)\s+ANSWER\b", re.IGNORECASE),
    ),
    (
        "solution_region",
        re.compile(r"\b(?:BEGIN|END)\s+SOLUTION\b", re.IGNORECASE),
    ),
    (
        "hidden_test_region",
        re.compile(
            r"\b(?:BEGIN|END)\s+HIDDEN\s+TESTS\b|"
            r"\bhidden\s+(?:auto)?grader\s+tests?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "instructor_release_directive",
        re.compile(r"\bremoved\s+from\s+(?:the\s+)?student\s+version\b", re.IGNORECASE),
    ),
)

ALLOWED_NOTEBOOK_NBGRADER_KEYS = {"assignment_id"}
ALLOWED_CELL_NBGRADER_KEYS = {
    "cell_type",
    "checksum",
    "grade",
    "grade_id",
    "locked",
    "points",
    "schema_version",
    "solution",
    "task",
}
REQUIRED_CELL_NBGRADER_KEYS = {
    "grade",
    "grade_id",
    "locked",
    "schema_version",
    "solution",
}

MEDIA_SUFFIXES = {
    ".apng",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".pdf",
    ".png",
    ".svg",
    ".webm",
    ".webp",
}

MARKDOWN_MEDIA_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))",
    re.IGNORECASE,
)
HTML_MEDIA_RE = re.compile(
    r"<(?:img|source|video|audio)[^>]+(?:src|href)=[\"']([^\"']+)",
    re.IGNORECASE,
)
CODE_MEDIA_RE = re.compile(
    r"(?:filename|fname|path)\s*=\s*[\"']"
    r"([^\"']+\.(?:apng|gif|jpe?g|mov|mp3|mp4|ogg|pdf|png|svg|webm|webp))"
    r"[\"']",
    re.IGNORECASE,
)
GENERIC_MEDIA_CONSTRUCTOR_RE = re.compile(
    r"\b(?:Image|Video|Audio)\(\s*(?:filename\s*=\s*)?[\"']"
    r"([^\"']+\.(?:apng|gif|jpe?g|mov|mp3|mp4|ogg|pdf|png|svg|webm|webp))"
    r"[\"']",
    re.IGNORECASE,
)

TEXT_RELEASE_SUFFIXES = {
    ".css",
    ".htm",
    ".html",
    ".jl",
    ".js",
    ".md",
    ".py",
    ".qmd",
    ".r",
    ".tex",
    ".txt",
}

STUDENT_PLACEHOLDER_RE = re.compile(
    r"YOUR\s+(?:ANSWER|CODE)\s+HERE|NotImplementedError|"
    r"BEGIN\s+(?:SOLUTION|ANSWER)",
    re.IGNORECASE,
)

SKIP_DIR_NAMES = {
    ".git",
    ".ipynb_checkpoints",
    "__pycache__",
}


@dataclass(frozen=True, order=True)
class Finding:
    severity: str
    code: str
    path: str
    locator: str = ""
    detail: str = ""

    def render(self) -> str:
        location = self.path
        if self.locator:
            location += f"::{self.locator}"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"{self.severity.upper()} {self.code} {location}{suffix}"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help="Public files or directories to check, relative to the repository root.",
    )
    parser.add_argument(
        "--release-root",
        type=Path,
        help="Path to aa_class for release/public byte comparison.",
    )
    parser.add_argument(
        "--fail-on-review",
        action="store_true",
        help="Deprecated compatibility flag; review findings fail closed by default.",
    )
    parser.add_argument(
        "--allow-review",
        action="store_true",
        help="Exploratory mode: return zero when only review findings remain.",
    )
    return parser.parse_args(argv)


def normalized_source(cell: dict) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return str(source)


def iter_selected_files(repo_root: Path, raw_paths: Sequence[str]) -> list[Path]:
    if raw_paths:
        roots = [(repo_root / raw).resolve() for raw in raw_paths]
    else:
        roots = [repo_root / name for name in ("aa_ss", "aa_u", "aa_x")]

    selected: set[Path] = set()
    for root in roots:
        try:
            root.relative_to(repo_root)
        except ValueError as exc:
            raise ValueError(f"path escapes repository root: {root}") from exc

        if not root.exists():
            raise FileNotFoundError(root)
        if root.is_file():
            selected.add(root)
            continue

        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name for name in dirnames if name not in SKIP_DIR_NAMES
            )
            base = Path(directory)
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                selected.add(base / filename)
    return sorted(selected)


def iter_selected_release_files(
    release_root: Path, raw_paths: Sequence[str]
) -> list[Path]:
    courses = {"aa_ss", "aa_u", "aa_x"}
    if raw_paths:
        roots: list[Path] = []
        for raw in raw_paths:
            parts = Path(raw).parts
            if not parts or parts[0] not in courses:
                continue
            roots.append(
                release_root / parts[0] / "release" / Path(*parts[1:])
            )
    else:
        roots = [release_root / course / "release" for course in sorted(courses)]

    selected: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        try:
            resolved.relative_to(release_root)
        except ValueError as exc:
            raise ValueError(f"release path escapes release root: {root}") from exc
        if not resolved.exists():
            continue
        if resolved.is_file():
            selected.add(resolved)
            continue
        for directory, dirnames, filenames in os.walk(resolved):
            dirnames[:] = sorted(
                name for name in dirnames if name not in SKIP_DIR_NAMES
            )
            base = Path(directory)
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                selected.add(base / filename)
    return sorted(selected)


def public_relative_from_release(path: Path, release_root: Path) -> str:
    parts = path.relative_to(release_root).parts
    if len(parts) < 3 or parts[1] != "release":
        raise ValueError(f"unexpected release path: {path}")
    return Path(parts[0], *parts[2:]).as_posix()


def source_counterpart(release_path: Path, release_root: Path) -> Path | None:
    parts = release_path.relative_to(release_root).parts
    if len(parts) < 3 or parts[1] != "release":
        return None
    return release_root / parts[0] / "source" / Path(*parts[2:])


def path_is_selected(path: Path, repo_root: Path, raw_paths: Sequence[str]) -> bool:
    if not raw_paths:
        return True
    target = path.resolve()
    for raw in raw_paths:
        selected = (repo_root / raw).resolve()
        if target == selected or selected in target.parents:
            return True
    return False


def release_counterpart(public_path: Path, repo_root: Path, release_root: Path) -> Path | None:
    relative = public_path.relative_to(repo_root)
    parts = relative.parts
    if len(parts) < 3 or parts[0] not in {"aa_ss", "aa_u", "aa_x"}:
        return None
    return release_root / parts[0] / "release" / Path(*parts[1:])


def local_reference(raw_reference: str) -> str | None:
    value = urllib.parse.unquote(raw_reference.strip())
    value = value.split("#", 1)[0].split("?", 1)[0]
    if not value:
        return None
    if value.lower().startswith("attachment:"):
        return value
    if re.match(r"^(?:https?:|data:|mailto:|javascript:|#|/)", value, re.IGNORECASE):
        return None
    if Path(value).suffix.lower() not in MEDIA_SUFFIXES:
        return None
    return value


def exact_case_resolution(base: Path, reference: str) -> tuple[str, str]:
    current = base
    mismatches: list[str] = []
    for part in Path(reference).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            current = current.parent
            continue
        try:
            names = os.listdir(current)
        except OSError:
            return "missing", reference
        if part in names:
            current /= part
            continue
        case_matches = sorted(name for name in names if name.casefold() == part.casefold())
        if case_matches:
            mismatches.append(f"{part}->{case_matches[0]}")
            current /= case_matches[0]
            continue
        return "missing", reference
    if mismatches:
        return "case_mismatch", ", ".join(mismatches)
    return "ok", reference


def media_references(source: str) -> Iterable[str]:
    for angle, plain in MARKDOWN_MEDIA_RE.findall(source):
        yield angle or plain
    yield from HTML_MEDIA_RE.findall(source)
    yield from CODE_MEDIA_RE.findall(source)
    yield from GENERIC_MEDIA_CONSTRUCTOR_RE.findall(source)


def check_text_release(path: Path, display_path: str) -> list[Finding]:
    relative = display_path
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [Finding("error", "text_read", relative, detail=type(exc).__name__)]

    findings: list[Finding] = []
    for code, pattern in CONTENT_RULES:
        for match in pattern.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            findings.append(Finding("error", code, relative, f"line[{line}]"))
    return findings


def check_notebook(path: Path, display_path: str) -> tuple[list[Finding], int]:
    relative = display_path
    findings: list[Finding] = []
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        findings.append(Finding("error", "notebook_parse", relative, detail=type(exc).__name__))
        return findings, 0

    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        findings.append(Finding("error", "notebook_shape", relative))
        return findings, 0

    notebook_nbgrader = notebook.get("metadata", {}).get("nbgrader")
    if notebook_nbgrader is not None:
        if not isinstance(notebook_nbgrader, dict):
            findings.append(Finding("error", "nbgrader_notebook_metadata_shape", relative))
        else:
            unknown = sorted(set(notebook_nbgrader) - ALLOWED_NOTEBOOK_NBGRADER_KEYS)
            if unknown:
                findings.append(
                    Finding(
                        "review",
                        "unknown_notebook_nbgrader_metadata",
                        relative,
                        detail=",".join(unknown),
                    )
                )
            assignment_id = notebook_nbgrader.get("assignment_id")
            if assignment_id is not None and (
                not isinstance(assignment_id, str) or not assignment_id.strip()
            ):
                findings.append(
                    Finding("error", "invalid_assignment_id", relative)
                )

    grade_ids: dict[str, int] = {}
    for index, cell in enumerate(notebook["cells"]):
        locator = f"cell[{index}]"
        if not isinstance(cell, dict):
            findings.append(Finding("error", "cell_shape", relative, locator))
            continue

        source = normalized_source(cell)
        for code, pattern in CONTENT_RULES:
            if pattern.search(source):
                findings.append(Finding("error", code, relative, locator))

        metadata = cell.get("metadata", {})
        nbgrader = metadata.get("nbgrader") if isinstance(metadata, dict) else None
        if nbgrader is not None:
            if not isinstance(nbgrader, dict):
                findings.append(Finding("error", "nbgrader_cell_metadata_shape", relative, locator))
                nbgrader = {}
            else:
                unknown = sorted(set(nbgrader) - ALLOWED_CELL_NBGRADER_KEYS)
                if unknown:
                    findings.append(
                        Finding(
                            "review",
                            "unknown_cell_nbgrader_metadata",
                            relative,
                            locator,
                            ",".join(unknown),
                        )
                    )
                missing = sorted(REQUIRED_CELL_NBGRADER_KEYS - set(nbgrader))
                if missing:
                    findings.append(
                        Finding(
                            "error",
                            "incomplete_student_nbgrader_metadata",
                            relative,
                            locator,
                            ",".join(missing),
                        )
                    )
                grade_id = nbgrader.get("grade_id")
                if not isinstance(grade_id, str) or not grade_id.strip():
                    findings.append(
                        Finding("error", "invalid_grade_id", relative, locator)
                    )
                else:
                    if grade_id in grade_ids:
                        findings.append(
                            Finding(
                                "error",
                                "duplicate_grade_id",
                                relative,
                                locator,
                                f"also cell[{grade_ids[grade_id]}]",
                            )
                        )
                    else:
                        grade_ids[grade_id] = index

                for key in ("grade", "locked", "solution", "task"):
                    if key in nbgrader and not isinstance(nbgrader[key], bool):
                        findings.append(
                            Finding(
                                "error",
                                "invalid_nbgrader_value",
                                relative,
                                locator,
                                key,
                            )
                        )
                schema_version = nbgrader.get("schema_version")
                if not isinstance(schema_version, int) or isinstance(
                    schema_version, bool
                ):
                    findings.append(
                        Finding(
                            "error",
                            "invalid_nbgrader_value",
                            relative,
                            locator,
                            "schema_version",
                        )
                    )
                points = nbgrader.get("points")
                if points is not None and (
                    not isinstance(points, (int, float)) or isinstance(points, bool)
                ):
                    findings.append(
                        Finding(
                            "error",
                            "invalid_nbgrader_value",
                            relative,
                            locator,
                            "points",
                        )
                    )
                cell_type = nbgrader.get("cell_type")
                if cell_type is not None and cell_type not in {
                    "code",
                    "markdown",
                    "raw",
                }:
                    findings.append(
                        Finding(
                            "error",
                            "invalid_nbgrader_value",
                            relative,
                            locator,
                            "cell_type",
                        )
                    )

                if (
                    nbgrader.get("solution") is True
                    and nbgrader.get("locked") is not True
                    and source.strip()
                    and not STUDENT_PLACEHOLDER_RE.search(source)
                ):
                    findings.append(
                        Finding(
                            "review",
                            "filled_solution_cell_review",
                            relative,
                            locator,
                            "student placeholder not recognized",
                        )
                    )

        outputs = cell.get("outputs", []) if cell.get("cell_type") == "code" else []
        if outputs:
            answer_cell = isinstance(nbgrader, dict) and (
                nbgrader.get("solution") is True or nbgrader.get("grade") is True
            )
            severity = "error" if answer_cell else "review"
            code = "answer_cell_saved_output" if answer_cell else "saved_output_review"
            findings.append(
                Finding(severity, code, relative, locator, f"output_count={len(outputs)}")
            )

        references = list(dict.fromkeys(media_references(source)))
        for raw_reference in references:
            reference = local_reference(raw_reference)
            if reference is None:
                continue
            if reference.lower().startswith("attachment:"):
                attachment = reference.split(":", 1)[1]
                attachments = cell.get("attachments", {})
                if not isinstance(attachments, dict) or attachment not in attachments:
                    findings.append(
                        Finding(
                            "error",
                            "missing_notebook_attachment",
                            relative,
                            locator,
                            reference,
                        )
                    )
                continue

            state, detail = exact_case_resolution(path.parent, reference)
            if state == "missing":
                findings.append(
                    Finding("error", "missing_media", relative, locator, reference)
                )
            elif state == "case_mismatch":
                findings.append(
                    Finding("error", "media_case_mismatch", relative, locator, detail)
                )

    return findings, len(notebook["cells"])


def check_source_release_structure(
    source_path: Path, release_path: Path, display_path: str
) -> list[Finding]:
    try:
        source_notebook = json.loads(source_path.read_text(encoding="utf-8"))
        release_notebook = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [
            Finding(
                "error",
                "source_release_parse",
                display_path,
                detail=type(exc).__name__,
            )
        ]

    source_cells = source_notebook.get("cells")
    release_cells = release_notebook.get("cells")
    if not isinstance(source_cells, list) or not isinstance(release_cells, list):
        return [Finding("error", "source_release_shape", display_path)]
    if len(source_cells) != len(release_cells):
        return [
            Finding(
                "review",
                "source_release_cell_count",
                display_path,
                detail=f"source={len(source_cells)} release={len(release_cells)}",
            )
        ]

    findings: list[Finding] = []
    comparable_fields = ("grade_id", "grade", "solution", "locked", "points", "task")
    for index, (source_cell, release_cell) in enumerate(
        zip(source_cells, release_cells)
    ):
        locator = f"cell[{index}]"
        if source_cell.get("id") != release_cell.get("id"):
            findings.append(
                Finding("review", "source_release_cell_id", display_path, locator)
            )
        if source_cell.get("cell_type") != release_cell.get("cell_type"):
            findings.append(
                Finding("review", "source_release_cell_type", display_path, locator)
            )

        source_nbgrader = source_cell.get("metadata", {}).get("nbgrader")
        release_nbgrader = release_cell.get("metadata", {}).get("nbgrader")
        if bool(source_nbgrader) != bool(release_nbgrader):
            findings.append(
                Finding(
                    "review",
                    "source_release_nbgrader_topology",
                    display_path,
                    locator,
                )
            )
            continue
        if isinstance(source_nbgrader, dict) and isinstance(release_nbgrader, dict):
            for field in comparable_fields:
                if source_nbgrader.get(field) != release_nbgrader.get(field):
                    findings.append(
                        Finding(
                            "review",
                            "source_release_nbgrader_value",
                            display_path,
                            locator,
                            field,
                        )
                    )

        source_text = normalized_source(source_cell)
        release_text = normalized_source(release_cell)
        is_solution = isinstance(source_nbgrader, dict) and (
            source_nbgrader.get("solution") is True
        )
        has_hidden_tests = bool(
            re.search(r"\bBEGIN\s+HIDDEN\s+TESTS\b", source_text, re.IGNORECASE)
        )
        if is_solution:
            if release_text.strip() and not STUDENT_PLACEHOLDER_RE.search(release_text):
                findings.append(
                    Finding(
                        "error",
                        "release_solution_not_cleared",
                        display_path,
                        locator,
                    )
                )
        elif not has_hidden_tests and source_text != release_text:
            findings.append(
                Finding(
                    "review",
                    "source_release_prompt_drift",
                    display_path,
                    locator,
                )
            )
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]

    try:
        files = iter_selected_files(repo_root, args.paths)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR selection {exc}", file=sys.stderr)
        return 1

    findings: list[Finding] = []
    checked_notebooks = 0
    checked_files = 0
    checked_release_notebooks = 0
    checked_release_files = 0
    answer_marked_text_stems: set[tuple[Path, str]] = set()
    release_answer_marked_text_stems: set[tuple[Path, str]] = set()

    for relative, reason in sorted(FORBIDDEN_PUBLIC_PATHS.items()):
        candidate = repo_root / relative
        if candidate.exists() and path_is_selected(candidate, repo_root, args.paths):
            findings.append(Finding("error", "forbidden_public_pdf", relative, detail=reason))

    release_root = args.release_root.resolve() if args.release_root else None
    release_files: list[Path] = []
    if release_root is not None:
        try:
            release_files = iter_selected_release_files(release_root, args.paths)
        except ValueError as exc:
            print(f"ERROR selection {exc}", file=sys.stderr)
            return 1
    for path in files:
        relative = path.relative_to(repo_root).as_posix()
        checked_files += 1

        if FORBIDDEN_NAME_RE.search(path.name):
            findings.append(Finding("error", "instructor_only_filename", relative))

        if path.suffix.lower() == ".ipynb":
            notebook_findings, cell_count = check_notebook(path, relative)
            findings.extend(notebook_findings)
            checked_notebooks += 1
        elif path.suffix.lower() in TEXT_RELEASE_SUFFIXES:
            text_findings = check_text_release(path, relative)
            findings.extend(text_findings)
            if any(
                finding.code in {
                    "legacy_answer_region",
                    "solution_region",
                    "hidden_test_region",
                    "instructor_release_directive",
                }
                for finding in text_findings
            ):
                answer_marked_text_stems.add((path.parent, path.stem))

        if release_root is not None:
            counterpart = release_counterpart(path, repo_root, release_root)
            if counterpart is not None:
                if counterpart.is_file():
                    if path.read_bytes() != counterpart.read_bytes():
                        findings.append(
                            Finding(
                                "error",
                                "release_public_drift",
                                relative,
                                detail=counterpart.as_posix(),
                            )
                        )
                elif path.suffix.lower() == ".ipynb":
                    findings.append(
                        Finding(
                            "review",
                            "missing_local_release_counterpart",
                            relative,
                            detail=counterpart.as_posix(),
                        )
                    )

    for path in release_files:
        public_relative = public_relative_from_release(path, release_root)
        display_path = f"release:{public_relative}"
        checked_release_files += 1

        if public_relative in FORBIDDEN_PUBLIC_PATHS:
            findings.append(
                Finding(
                    "error",
                    "forbidden_local_release_pdf",
                    display_path,
                    detail=FORBIDDEN_PUBLIC_PATHS[public_relative],
                )
            )
        if FORBIDDEN_NAME_RE.search(path.name):
            findings.append(
                Finding("error", "instructor_only_filename", display_path)
            )

        if path.suffix.lower() == ".ipynb":
            notebook_findings, cell_count = check_notebook(path, display_path)
            findings.extend(notebook_findings)
            checked_release_notebooks += 1
            source_path = source_counterpart(path, release_root)
            if source_path is not None and source_path.is_file():
                findings.extend(
                    check_source_release_structure(
                        source_path, path, display_path
                    )
                )
            else:
                findings.append(
                    Finding(
                        "review",
                        "missing_source_counterpart_for_release",
                        display_path,
                        detail=source_path.as_posix() if source_path else "unmapped",
                    )
                )
        elif path.suffix.lower() in TEXT_RELEASE_SUFFIXES:
            text_findings = check_text_release(path, display_path)
            findings.extend(text_findings)
            if any(
                finding.code in {
                    "legacy_answer_region",
                    "solution_region",
                    "hidden_test_region",
                    "instructor_release_directive",
                }
                for finding in text_findings
            ):
                release_answer_marked_text_stems.add((path.parent, path.stem))

    for path in files:
        if path.suffix.lower() != ".pdf":
            continue
        if (path.parent, path.stem) in answer_marked_text_stems:
            relative = path.relative_to(repo_root).as_posix()
            findings.append(
                Finding(
                    "review",
                    "pdf_companion_to_answer_marked_text",
                    relative,
                    detail="same-stem textual artifact contains instructor markers",
                )
            )

    for path in release_files:
        if path.suffix.lower() != ".pdf":
            continue
        if (path.parent, path.stem) in release_answer_marked_text_stems:
            display_path = (
                f"release:{public_relative_from_release(path, release_root)}"
            )
            findings.append(
                Finding(
                    "review",
                    "pdf_companion_to_answer_marked_text",
                    display_path,
                    detail="same-stem textual artifact contains instructor markers",
                )
            )

    unique_findings = sorted(set(findings))
    for finding in unique_findings:
        print(finding.render())

    errors = sum(finding.severity == "error" for finding in unique_findings)
    reviews = sum(finding.severity == "review" for finding in unique_findings)
    print(
        "SUMMARY "
        f"errors={errors} reviews={reviews} "
        f"checked_public_files={checked_files} "
        f"checked_public_notebooks={checked_notebooks} "
        f"checked_release_files={checked_release_files} "
        f"checked_release_notebooks={checked_release_notebooks}"
    )
    if errors:
        return 1
    if reviews and not args.allow_review:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
