import sys
sys.path.insert(0, ".")
from app.engine.prompt_utils import coerce_prompt_text

assert coerce_prompt_text("古风，勇峻山，8k") == "古风，勇峻山，8k"
assert coerce_prompt_text("  A pavilion, 8k  ") == "A pavilion, 8k"
assert coerce_prompt_text(None) is None
assert coerce_prompt_text(b"english \xe2\x80\x94 mix") == "english \u2014 mix"
assert coerce_prompt_text(["古风", ["mist", b"bytes", 8], {"a": 1}]) == "古风, mist, bytes, 8, {'a': 1}"
assert coerce_prompt_text(("mixed", "pavilion")) == "mixed, pavilion"
assert coerce_prompt_text("") == ""
assert coerce_prompt_text([]) == ""
print("coerce_prompt_text: ALL PASS")
