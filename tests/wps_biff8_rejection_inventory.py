"""Maintenance-only deterministic BIFF8 rejection-site inventory.

Does not read input workbooks, execute code, or relax the parser.
"""
import ast
import json
from pathlib import Path

PARSER = Path(__file__).resolve().parents[1] / "skills" / "wps" / "scripts" / "wps_biff8.py"
RISK_TERMS = {
    "structure": ("BOF", "EOF", "BoundSheet", "Date1904", "WsBool", "offset", "length"),
    "security": ("FILEPASS", "encrypted", "macro", "password"),
    "saved_values": ("Formula", "SST", "cell", "String", "RK", "Number"),
}

def classify(message):
    matched = [key for key, terms in RISK_TERMS.items()
               if any(term.lower() in message.lower() for term in terms)]
    return matched or ["unclassified_requires_manual_review"]

def inventory(source):
    tree = ast.parse(source)
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name not in ("require", "UnsupportedError", "ValueError"):
            continue
        literal = next((a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)), None)
        if literal is None:
            continue
        sites.append({"line": node.lineno, "guard": name, "message": literal,
                      "risk_categories": classify(literal),
                      "safe_to_skip": False, "decision": "review_only"})
    return {"schema_version": "biff8_rejection_inventory_v1",
            "policy": "no automatic unknown-record skipping",
            "site_count": len(sites),
            "sites": sorted(sites, key=lambda x: (x["line"], x["guard"]))}

def main():
    data = inventory(PARSER.read_text(encoding="utf-8"))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
