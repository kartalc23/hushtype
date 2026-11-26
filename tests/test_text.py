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


def test_vocabulary_is_truncated(tmp_path):
    f = tmp_path / "v.txt"
    f.write_text("# header\n" + "\n".join(f"term{i}" for i in range(50)))
    logs = []
    result = text.load_vocabulary(f, 35, logs.append)
    assert result.startswith("term0, term1")
    assert "term34" in result and "term35" not in result
    assert "35 of 50" in logs[0]


def test_style_is_truncated_to_words(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("# ignored\n" + " ".join(["word"] * 40))
    assert len(text.load_style(f, 35, lambda _: None).split()) == 35


def test_missing_files_are_fine(tmp_path):
    missing = tmp_path / "nope.txt"
    assert text.load_vocabulary(missing, 35, print) == ""
    assert text.load_style(missing, 35, print) == ""
    assert text.load_replacements(missing, print) == []
