"""Compatibility shim re-exporting read_et interfaces for existing tests and modules."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_et import (  # noqa: F401
    MAX_JSON_OUTPUT_BYTES,
    ParseError,
    _read_bytes,
    check_environment,
    encode_result,
    failure,
    main,
    read,
    read_bytes,
    require,
)

if __name__ == '__main__':
    sys.exit(main())
