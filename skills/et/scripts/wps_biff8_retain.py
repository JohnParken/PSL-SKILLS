# SPDX-License-Identifier: MIT
"""Small shared retention helpers for the split BIFF8 worksheet readers."""
from wps_biff8_values import ContinuedStrings

OBJ_NOTE_PREFIX = b'\x15\x00\x12\x00'
NOTE_RECORD_NAMES = {0x00EC: 'MsoDrawingNoteFragmentOpaque',
                     0x005D: 'ObjNoteOpaque',
                     0x01B6: 'TxONoteOpaque',
                     0x003C: 'NoteTextOrRunsOpaque',
                     0x001C: 'NoteLinkOpaque'}


def strings(reader, records, index, position=0):
    """Continued string reader charged against the reader metadata ceiling."""
    return ContinuedStrings(records, index, position,
                            metadata_limit=reader.limits['max_metadata_bytes'])


def retain(reader, record, name, scope, **structure):
    """Retain one known metadata record and attach optional structure."""
    index = reader.metadata_record(record, {record[1]: name}, scope)
    if index is not None and structure:
        reader.metadata[index].update(structure)
    return index


def salvage(reader, record, scope, *, name=None, code=None, message=None,
            may_affect=True):
    """Retain an uninterpretable record without aborting the workbook."""
    return reader.salvage_record(record, scope, name=name, code=code,
                                 message=message,
                                 may_affect_cell_values=may_affect)


def salvage_metadata(reader, record, scope, *, name=None, code=None,
                     message=None):
    """Retain known non-cell metadata without downgrading value coverage.

    The record cannot carry saved worksheet cell values by its BIFF8 identity.
    Its diagnostics still make the result partial, but malformed metadata must
    not be reported as a cell/value omission.
    """
    return salvage(reader, record, scope, name=name, code=code,
                   message=message, may_affect=False)
