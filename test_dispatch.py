"""Tests for dispatch.py — channel parsing, summary loading, email theming."""

import json
import os
import re

from dispatch import (
    _markdown_to_html,
    _normalize_mode,
    load_email_theme,
    load_summary,
    parse_channels,
)


class TestParseChannels:
    def test_single_channel(self):
        yaml = """
        - name: team
          type: telegram
          chat_id: "-100123"
          mode: private
        """
        channels = parse_channels(yaml)
        assert len(channels) == 1
        assert channels[0]["name"] == "team"
        assert channels[0]["type"] == "telegram"
        assert channels[0]["chat_id"] == "-100123"
        assert channels[0]["mode"] == "private"

    def test_multiple_channels(self):
        yaml = """
        - name: team
          type: telegram
          chat_id: "-100123"
          mode: private

        - name: public
          type: telegram
          chat_id: "@mychannel"
          mode: public

        - name: discord-dev
          type: discord
          webhook_url_env: DISCORD_WEBHOOK
          mode: private
        """
        channels = parse_channels(yaml)
        assert len(channels) == 3
        assert channels[0]["name"] == "team"
        assert channels[1]["name"] == "public"
        assert channels[1]["chat_id"] == "@mychannel"
        assert channels[2]["type"] == "discord"

    def test_empty_input(self):
        assert parse_channels("") == []
        assert parse_channels("   ") == []

    def test_comments_ignored(self):
        yaml = """
        # This is a comment
        - name: team
          type: telegram
          # inline comment
          chat_id: "-100123"
          mode: private
        """
        channels = parse_channels(yaml)
        assert len(channels) == 1
        assert channels[0]["name"] == "team"

    def test_quoted_values_stripped(self):
        yaml = """
        - name: "team chat"
          type: 'telegram'
          chat_id: "-100123"
          mode: private
        """
        channels = parse_channels(yaml)
        assert channels[0]["name"] == "team chat"
        assert channels[0]["type"] == "telegram"

    def test_thread_id_preserved(self):
        yaml = """
        - name: team
          type: telegram
          chat_id: "-100123"
          thread_id: 4
          mode: private
        """
        channels = parse_channels(yaml)
        assert channels[0]["thread_id"] == "4"

    def test_custom_bot_token_env(self):
        yaml = """
        - name: alerts
          type: telegram
          chat_id: "-100123"
          bot_token_env: ALERT_BOT_TOKEN
          mode: private
        """
        channels = parse_channels(yaml)
        assert channels[0]["bot_token_env"] == "ALERT_BOT_TOKEN"


class TestNormalizeMode:
    def test_new_names_pass_through(self):
        assert _normalize_mode("dev") == "dev"
        assert _normalize_mode("community") == "community"

    def test_old_names_aliased(self):
        assert _normalize_mode("private") == "dev"
        assert _normalize_mode("public") == "community"

    def test_unknown_passes_through(self):
        assert _normalize_mode("custom") == "custom"


class TestLoadSummary:
    def test_returns_empty_for_missing_file(self):
        assert load_summary("nonexistent_mode_xyz") == ""

    def test_loads_content(self):
        with open("/tmp/summary_testmode.md", "w") as f:
            f.write("📦 **My Update**\n\n🔧 Fixed a bug\n🚀 Added a feature")

        content = load_summary("testmode")
        assert "My Update" in content
        assert "Fixed a bug" in content
        assert "Added a feature" in content
        os.unlink("/tmp/summary_testmode.md")

    def test_strips_whitespace(self):
        with open("/tmp/summary_striptest.md", "w") as f:
            f.write("  \n  content here  \n  ")

        content = load_summary("striptest")
        assert content == "content here"
        os.unlink("/tmp/summary_striptest.md")


class TestStatusPills:
    """Leading status tokens on task-list bullets render as coloured chips."""

    def test_all_five_statuses_chip(self):
        md = (
            "- UPDATED - T154: a\n"
            "- **NEW** - T200: b\n"
            "- DONE - T199: c\n"
            "- IN PROGRESS - T181: d\n"
            "- BLOCKED - T178: e\n"
        )
        html = _markdown_to_html(md)
        labels = re.findall(r'text-transform:uppercase;">([^<]+)</span>', html)
        assert labels == ["Updated", "New", "Done", "In Progress", "Blocked"]

    def test_no_bare_token_survives(self):
        html = _markdown_to_html("- DONE - T1: x\n")
        assert not re.findall(r"<li[^>]*>\s*(?:<strong>)?\s*DONE\b", html)

    def test_all_three_dashes(self):
        for dash in ["-", "\u2013", "\u2014"]:
            html = _markdown_to_html(f"- DONE {dash} T1: x\n")
            assert 'uppercase;">Done</span>' in html, dash

    def test_bullet_without_status_untouched(self):
        html = _markdown_to_html("- Just a normal bullet.\n")
        assert "Just a normal bullet." in html
        assert "text-transform:uppercase" not in html

    def test_blocked_plus_waiting_on_yields_one_chip(self):
        html = _markdown_to_html("- BLOCKED - T1: x (waiting on Ada).\n")
        assert html.count('uppercase;">Blocked</span>') == 1
        assert "waiting on Ada" in html

    def test_inline_markers_still_work(self):
        html = _markdown_to_html(
            "- \u2705 Shipped: T1 - a\n- T2 - b (in progress)\n- T3 - c (waiting on Ada)\n"
        )
        assert 'uppercase;">DONE</span>' in html
        assert 'uppercase;">In Progress</span>' in html
        assert 'uppercase;">Blocked</span>' in html


class TestEmailTheme:
    """digest_theme maps a board-theme.json palette onto the email."""

    BOARD = {
        "colors": {
            "gold": "#000584", "gold_bright": "#00a8e1", "gold_pale": "#e3f6ff",
            "ink": "#04001e", "body": "#221d40", "muted": "#656c85",
            "border": "#dbe4f0", "paper": "#f2f8fe",
        }
    }

    def test_default_is_the_shipped_palette(self):
        t = load_email_theme("")
        assert t["colors"]["accent"] == "#7e6717"
        assert t["colors"]["rule"] == "#c9bfa8"

    def test_board_vocabulary_maps_to_email_roles(self):
        t = load_email_theme(json.dumps(self.BOARD))
        assert t["colors"]["accent"] == "#000584"
        assert t["colors"]["accent_soft"] == "#00a8e1"
        assert t["colors"]["ink"] == "#04001e"
        assert t["colors"]["surface"] == "#f2f8fe"
        assert t["colors"]["surface_alt"] == "#e3f6ff"

    def test_rule_inherits_border_when_theme_omits_it(self):
        t = load_email_theme(json.dumps(self.BOARD))
        assert t["colors"]["rule"] == "#dbe4f0"

    def test_status_chips_stay_semantic_under_a_brand_palette(self):
        t = load_email_theme(json.dumps(self.BOARD))
        assert t["status"]["blocked"]["bg"] == "#f4d4d4"
        assert t["status"]["done"]["fg"] == "#1f5b2c"

    def test_direct_role_key_beats_board_alias(self):
        t = load_email_theme(json.dumps({"colors": {"gold": "#111111", "accent": "#222222"}}))
        assert t["colors"]["accent"] == "#222222"

    def test_status_override_is_partial(self):
        t = load_email_theme(json.dumps({"status": {"BLOCKED": {"bg": "#001122"}}}))
        assert t["status"]["blocked"]["bg"] == "#001122"
        assert t["status"]["blocked"]["fg"] == "#7e1f1f"

    def test_dedupe_follows_themed_blocked_colour(self):
        # Guards the de-dupe sentinel against being pinned to a literal colour.
        t = load_email_theme(json.dumps({"status": {"blocked": {"bg": "#001122"}}}))
        html = _markdown_to_html("- BLOCKED - T1: x (waiting on Ada).\n", t)
        assert html.count("background:#001122") == 1
        assert "waiting on Ada" in html

    def test_malformed_theme_falls_back_instead_of_raising(self):
        for bad in ["{not json", "[]", "null", '{"colors": "nope"}']:
            assert load_email_theme(bad)["colors"]["accent"] == "#7e6717"

    def test_colour_with_backslash_is_not_a_group_reference(self):
        t = load_email_theme(json.dumps({"colors": {"gold": r"#fff\1"}}))
        html = _markdown_to_html("- \u2705 Shipped: T1 - a\n", t)
        assert 'uppercase;">DONE</span>' in html
