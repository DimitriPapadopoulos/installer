"""Core wheel installation logic."""

import posixpath
from io import BytesIO
from typing import cast

from installer.destinations import WheelDestination
from installer.exceptions import InvalidWheelSource
from installer.records import RecordEntry
from installer.sources import WheelSource
from installer.utils import SCHEME_NAMES, Scheme, parse_entrypoints, parse_metadata_file

__all__ = ["install"]


def _process_WHEEL_file(source: WheelSource) -> Scheme:  # noqa: N802
    """Process the WHEEL file, from ``source``.

    Returns the scheme that the archive root should go in.
    """
    stream = source.read_dist_info("WHEEL")
    metadata = parse_metadata_file(stream)

    # Ensure compatibility with this wheel version.
    if not (metadata["Wheel-Version"] and metadata["Wheel-Version"].startswith("1.")):
        message = "Incompatible Wheel-Version {}, only support version 1.x wheels."
        raise InvalidWheelSource(source, message.format(metadata["Wheel-Version"]))

    # Determine where archive root should go.
    if metadata["Root-Is-Purelib"] == "true":
        return cast("Scheme", "purelib")
    else:
        return cast("Scheme", "platlib")


def _determine_scheme(
    path: str, source: WheelSource, root_scheme: Scheme
) -> tuple[Scheme, str]:
    """Determine which scheme to place given path in, from source."""
    data_dir = source.data_dir

    # If it's in not `{distribution}-{version}.data`, then it's in root_scheme.
    if posixpath.commonprefix([data_dir, path]) != data_dir:
        return root_scheme, path

    # Figure out which scheme this goes to.
    parts = []
    scheme_name = None
    left = path
    while True:
        left, right = posixpath.split(left)
        parts.append(right)
        if left == source.data_dir:
            scheme_name = right
            break

    if scheme_name not in SCHEME_NAMES:
        msg_fmt = "{path} is not contained in a valid .data subdirectory."
        raise InvalidWheelSource(source, msg_fmt.format(path=path))

    return cast("Scheme", scheme_name), posixpath.join(*reversed(parts[:-1]))


def install(
    source: WheelSource,
    destination: WheelDestination,
    additional_metadata: dict[str, bytes],
) -> None:
    """Install wheel described by ``source`` into ``destination``.

    :param source: wheel to install.
    :param destination: where to write the wheel.
    :param additional_metadata: additional metadata files to generate, usually
                                generated by the caller.

    """
    root_scheme = _process_WHEEL_file(source)

    # RECORD handling
    record_file_path = posixpath.join(source.dist_info_dir, "RECORD")
    written_records = []

    # Write the entry_points based scripts.
    if "entry_points.txt" in source.dist_info_filenames:
        entrypoints_text = source.read_dist_info("entry_points.txt")
        for name, module, attr, section in parse_entrypoints(entrypoints_text):
            record = destination.write_script(
                name=name,
                module=module,
                attr=attr,
                section=section,
            )
            written_records.append((Scheme("scripts"), record))

    # Write all the files from the wheel.
    for record_elements, stream, is_executable in source.get_contents():
        source_record = RecordEntry.from_elements(*record_elements)
        path = source_record.path
        # Skip the RECORD, which is written at the end, based on this info.
        if path == record_file_path:
            continue

        # Figure out where to write this file.
        scheme, destination_path = _determine_scheme(
            path=path,
            source=source,
            root_scheme=root_scheme,
        )
        record = destination.write_file(
            scheme=scheme,
            path=destination_path,
            stream=stream,
            is_executable=is_executable,
        )
        written_records.append((scheme, record))

    # Write all the installation-specific metadata
    for filename, contents in additional_metadata.items():
        path = posixpath.join(source.dist_info_dir, filename)

        with BytesIO(contents) as other_stream:
            record = destination.write_file(
                scheme=root_scheme,
                path=path,
                stream=other_stream,
                is_executable=False,
            )
        written_records.append((root_scheme, record))

    written_records.append((root_scheme, RecordEntry(record_file_path, None, None)))
    destination.finalize_installation(
        scheme=root_scheme,
        record_file_path=record_file_path,
        records=written_records,
    )
