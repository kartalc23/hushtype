from hushtype import text


def test_replacements_respect_word_boundaries():
    rules = text.parse_replacements("1910 => ACME\n")
    assert text.apply_replacements("at 1910 today", rules) == "at ACME today"
    assert text.apply_replacements("1910's plan", rules) == "ACME's plan"
    assert text.apply_replacements("31910", rules) == "31910"


def test_replacements_are_case_insensitive():
    rules = text.parse_replacements("post gres => Postgres")
    assert text.apply_replacements("I use Post Gres daily", rules) == "I use Postgres daily"


def test_replacements_ignore_comments_and_junk():
    rules = text.parse_replacements("# comment\n\nno arrow here\n => empty\nfoo => bar\n")
    assert len(rules) == 1


def test_replacement_backslashes_are_literal():
    rules = text.parse_replacements(r"path => C:\new")
    assert text.apply_replacements("path", rules) == r"C:\new"


def words(s):  # stand-in tokenizer: one token per word
    return len(s.split())


def test_prompt_fills_budget_in_order():
    terms = [f"t{i}" for i in range(50)]
    p = text.build_prompt("", terms, words, budget=10)
    assert p.terms_used == 10 and p.hotwords.startswith("t0, t1")
    assert p.dropped[0] == "t10" and p.tokens <= 10


def test_style_takes_at_most_half_the_budget():
    p = text.build_prompt("a " * 40, ["x", "y"], words, budget=20)
    assert p.style_tokens == 10
    assert p.terms_used == 2


def test_prompt_with_nothing():
    p = text.build_prompt("", [], words, budget=224)
    assert (p.style, p.hotwords, p.tokens) == ("", "", 0)


def test_read_files_skip_comments_and_duplicates(tmp_path):
    v = tmp_path / "v.txt"
    v.write_text("# header\nAlpha\n\nbeta\nalpha\n")
    assert text.read_terms(v) == ["Alpha", "beta"]
    s = tmp_path / "s.txt"
    s.write_text("# c\nHello   there.\nSecond line.\n")
    assert text.read_style(s) == "Hello there. Second line."


def test_round_trip_writers(tmp_path):
    v, s, r = tmp_path / "v.txt", tmp_path / "s.txt", tmp_path / "r.txt"
    text.write_terms(v, ["One", " ", "Two"])
    text.write_style(s, "  How I talk.  ")
    text.write_replacement_pairs(r, [("foo", "bar"), ("", "x"), ("a b", "c")])
    assert text.read_terms(v) == ["One", "Two"]
    assert text.read_style(s) == "How I talk."
    assert text.read_replacement_pairs(r) == [("foo", "bar"), ("a b", "c")]


def test_missing_files_are_fine(tmp_path):
    missing = tmp_path / "nope.txt"
    assert text.read_terms(missing) == []
    assert text.read_style(missing) == ""
    assert text.read_replacement_pairs(missing) == []
