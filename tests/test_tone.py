import pytest

from hushtype import tone


@pytest.mark.parametrize("app_id,title,expected", [
    ("org.mozilla.Thunderbird", "Inbox", "email"),
    ("thunderbird", "", "email"),
    ("org.kde.kmail2", "", "email"),
    ("Slack", "general", "work"),
    ("com.slack.Slack", "", "work"),
    ("teams-for-linux", "", "work"),
    ("org.telegram.desktop", "Saved Messages", "personal"),
    ("TelegramDesktop", "", "personal"),
    ("discord", "", "personal"),
    ("signal", "", "personal"),
    ("com.mitchellh.ghostty", "", "other"),
    ("org.kde.kate", "notes.txt", "other"),
    ("io.elementary.code", "", "other"),          # not "element"
    ("im.riot.Riot", "", "work"),
    ("", "", "other"),
])
def test_classify_apps(app_id, title, expected):
    assert tone.classify(app_id, title) == expected


@pytest.mark.parametrize("app_id,title,expected", [
    ("firefox", "Inbox (3) - you@example.com - Gmail - Mozilla Firefox", "email"),
    ("google-chrome", "general | Acme - Slack", "work"),
    ("chromium", "WhatsApp", "personal"),
    ("brave-browser", "Discord | #random", "personal"),
    ("crx_abcdef", "WhatsApp Web", "personal"),
    ("org.mozilla.firefox", "Signal processing basics - Wikipedia", "other"),
    ("firefox", "slackware linux", "other"),       # whole words only
    ("firefox", "Hacker News", "other"),
    ("google-chrome", "Gelen Kutusu (5) - ad@sirket.com.tr - Şirket Posta", "email"),  # Workspace
    ("google-chrome", "Inbox (2) - me@acme.io - Acme Mail", "email"),
    ("google-chrome", "Posta - Son dakika haberler", "other"),                   # a newspaper
    ("org.kde.kate", "notes - me@acme.io", "other"),                             # not a browser
])
def test_classify_browser_titles(app_id, title, expected):
    assert tone.classify(app_id, title) == expected


def test_title_ignored_outside_browsers():
    assert tone.classify("org.kde.kate", "gmail-notes.txt") == "other"


def test_custom_rules_win():
    custom = {"work": ["discord"], "other": ["slack"]}
    assert tone.classify("discord", "", custom) == "work"
    assert tone.classify("Slack", "", custom) == "other"
    assert tone.classify("firefox", "Jira board", {"work": ["jira"]}) == "work"


T = tone.BUILTIN_TONES


def style(**kw):
    return tone.ToneStyle(**kw)


def test_default_leaves_text():
    assert tone.apply("  Hello there.  ", T["default"]) == "Hello there."


def test_formal():
    assert tone.apply("see you tomorrow", T["formal"]) == "See you tomorrow."
    assert tone.apply("Is it ready?", T["formal"]) == "Is it ready?"
    assert tone.apply("\"quoted\"", T["formal"]) == "\"Quoted\""
    assert tone.apply("done. next one", T["formal"]) == "Done. Next one."


def test_casual_drops_period_of_short_messages():
    assert tone.apply("Sounds good.", T["casual"]) == "Sounds good"
    assert tone.apply("Sure. See you then.", T["casual"]) == "Sure. See you then"
    assert tone.apply("One. Two. Three.", T["casual"]) == "One. Two. Three."
    assert tone.apply("Really?", T["casual"]) == "Really?"
    assert tone.apply("Well...", T["casual"]) == "Well..."
    assert tone.apply("One. Two. Three.", style(final_period="remove_short", short_sentences=3)) == \
        "One. Two. Three"


def test_very_casual():
    vc = T["very_casual"]
    assert tone.apply("Sounds good. See you at 5.", vc) == "sounds good. see you at 5"
    assert tone.apply("I think so. NASA agrees.", vc) == "I think so. NASA agrees"
    assert tone.apply("Alex says hi.", vc, names=["Alex"]) == "Alex says hi"
    assert tone.apply("Hello Anna.", vc) == "hello Anna"


def test_turkish_dotted_letters():
    vc = T["very_casual"]
    assert tone.apply("İyi geceler.", vc, language="tr") == "iyi geceler"
    assert tone.apply("Işık kapalı.", vc, language="tr") == "ışık kapalı"
    assert tone.apply("Is it on?", vc, language="en") == "is it on?"
    assert tone.apply("istanbul güzel", T["formal"], language="tr") == "İstanbul güzel."


def test_tone_replacements():
    st = style(replacements=["going to => gonna", "want to => wanna"])
    assert tone.apply("I'm going to call, want to come?", st) == "I'm gonna call, wanna come?"


EMAIL = T["email"]


def test_email_layout():
    out = tone.apply("Hi John, thanks for the update. I will send the files tomorrow. Best regards, Alex",
                     EMAIL)
    assert out == "Hi John,\n\nThanks for the update. I will send the files tomorrow.\n\nBest regards,\nAlex"


def test_email_signature_and_paragraphs():
    st = style(case="capitalize", final_period="add", layout="email", signature="Alex\nAcme")
    out = tone.apply("hey team, quick update. new paragraph, the release is ready. thanks!", st)
    assert out == "Hey team,\n\nQuick update.\n\nThe release is ready.\n\nThanks,\nAlex\nAcme"
    # the signature only goes under a closing, so it never lands mid-email
    assert tone.apply("Please see the report.", st) == "Please see the report."


def test_email_turkish():
    out = tone.apply("Merhaba Ahmet Bey, dosyaları yarın gönderiyorum. Yeni paragraf. "
                     "Bir sorunuz olursa yazın. Saygılarımla.", EMAIL, language="tr")
    assert out == ("Merhaba Ahmet Bey,\n\nDosyaları yarın gönderiyorum.\n\n"
                   "Bir sorunuz olursa yazın.\n\nSaygılarımla")


def test_email_turkish_word_order():
    # greeting word last ("Ahmet Bey merhaba") and a closing with a verb ("dilerim")
    out = tone.apply("Ahmet Bey merhaba. Nasılsınız? Umarım iyisinizdir. Düzenlemeleri tamamladık. "
                     "İyi çalışmalar dilerim.", EMAIL, language="tr")
    assert out == ("Ahmet Bey merhaba,\n\nNasılsınız? Umarım iyisinizdir. Düzenlemeleri tamamladık."
                   "\n\nİyi çalışmalar dilerim")
    st = tone.ToneStyle(case="capitalize", final_period="add", layout="email", signature="Alex")
    parts = [tone.apply("Ahmet Bey merhaba.", st, language="tr"),
             tone.apply("İstediğiniz düzenlemeleri tamamladık.", st, language="tr", continuing=True),
             tone.apply("İyi çalışmalar dilerim.", st, language="tr", continuing=True)]
    assert "".join(parts) == ("Ahmet Bey merhaba,\n\nİstediğiniz düzenlemeleri tamamladık.\n\n"
                              "İyi çalışmalar dilerim,\nAlex")
    # a short sentence that merely ends in a greeting-like word further in is not a greeting
    assert "\n" not in tone.apply("Toplantı yarın saat üçte, herkes gelsin.", EMAIL, language="tr")


def test_email_not_fooled():
    # "Thanks for your help" is a sentence, not a closing followed by a name
    assert tone.apply("Hi John. Thanks for your help.", EMAIL) == "Hi John,\n\nThanks for your help."
    # no greeting word at the start: nothing is split off
    assert tone.apply("The meeting moved to 3. See you there.", EMAIL) == \
        "The meeting moved to 3. See you there."
    # a long first clause is not a greeting
    out = tone.apply("Hello and welcome to the quarterly planning meeting notes, here they are.", EMAIL)
    assert "\n" not in out


def test_email_in_pieces():
    st = tone.ToneStyle(case="capitalize", final_period="add", layout="email", signature="Alex")
    parts = [tone.apply("Merhaba Ahmet Bey", st, language="tr"),
             tone.apply("dosyaları yarın gönderiyorum.", st, language="tr", continuing=True),
             tone.apply("Bir sorunuz olursa yazın.", st, language="tr", continuing=True),
             tone.apply("İyi çalışmalar.", st, language="tr", continuing=True)]
    assert "".join(parts) == ("Merhaba Ahmet Bey,\n\nDosyaları yarın gönderiyorum.\n\n"
                              "Bir sorunuz olursa yazın.\n\nİyi çalışmalar,\nAlex")
    # a greeting starts a new email even in the same window: no blank lines in front
    assert tone.apply("Hi Anna, quick question.", st, continuing=True) == "Hi Anna,\n\nQuick question."


def test_continuing_only_affects_email_layout():
    assert tone.apply("sounds good.", T["casual"], continuing=True) == "sounds good"


def test_custom_closing():
    st = style(layout="email", closings=["cheers mate"])
    assert tone.apply("All done. Cheers mate", st) == "All done.\n\nCheers mate"


def test_validate_style():
    with pytest.raises(ValueError):
        tone.validate_style("x", style(case="shout"))
    with pytest.raises(ValueError):
        tone.validate_style("x", style(short_sentences=0))


def test_count_sentences():
    assert tone.count_sentences("") == 0
    assert tone.count_sentences("Version 2.5 is out") == 1
    assert tone.count_sentences("Hi. How are you? Fine!") == 3
