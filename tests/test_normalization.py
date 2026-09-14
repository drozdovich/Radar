from __future__ import annotations

import unittest
import copy

from telegram_project_radar.normalization import (
    approved_title_matches,
    extract_embedded_urls,
    extract_text,
    normalize_message,
    normalize_title,
)


class NormalizationTests(unittest.TestCase):
    def test_rich_message_keeps_nested_text_caption_and_links_not_file_metadata(self):
        content = {'@type': 'messageRichMessage', 'message': {'is_full': True, 'blocks': [
            {'@type': 'pageBlockHeader', 'header': {'@type': 'richTextPlain', 'text': 'Incoming calls'}},
            {'@type': 'pageBlockParagraph', 'text': {'@type': 'richTexts', 'texts': [
                {'@type': 'richTextPlain', 'text': 'Need '},
                {'@type': 'richTextBold', 'text': {'@type': 'richTextPlain', 'text': 'automation'}},
                {'@type': 'richTextUrl', 'text': {'@type': 'richTextPlain', 'text': ' details'}, 'url': 'https://example.com/job'}]}},
            {'@type': 'pageBlockPhoto', 'photo': {'data': 'NOT TEXT', 'text': 'NOT TEXT'},
             'caption': {'@type': 'pageBlockCaption', 'text': {'@type': 'richTextPlain', 'text': 'Call centre'}}}]}}
        self.assertEqual(extract_text(content)[0], 'Incoming calls\nNeed automation details\nCall centre')
        self.assertEqual(extract_embedded_urls(content), ('https://example.com/job',))
        content['message']['is_full'] = False
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            extract_text(content)
        content['message']['is_full'] = True
        content['message']['blocks'].append({'@type': 'pageBlockUnsupported'})
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            extract_text(content)

    def test_poll_text_is_complete_and_stable_when_vote_counts_change(self):
        content = {'@type': 'messagePoll', 'poll': {
            'question': {'@type': 'formattedText', 'text': 'Who needs a dialer?'},
            'options': [{'text': 'Yes', 'voter_count': 1}, {'text': {'@type': 'formattedText', 'text': 'No'}}],
            'type': {'@type': 'pollTypeQuiz', 'explanation': {'text': 'Explanation'}}},
            'description': {'@type': 'formattedText', 'text': 'Survey https://example.com'}}
        self.assertEqual(extract_text(content)[0], 'Who needs a dialer?\nSurvey https://example.com\nYes\nNo\nExplanation')
        changed = copy.deepcopy(content)
        changed['poll']['options'][0]['voter_count'] = 99
        self.assertEqual(extract_text(changed), extract_text(content))
        self.assertEqual(extract_embedded_urls(content), ('https://example.com',))

    def test_poll_option_and_checklist_keep_displayed_text(self):
        self.assertEqual(extract_text({'@type': 'messagePollOptionAdded', 'text': {'text': 'Hire operator'}})[0], 'Hire operator')
        self.assertEqual(extract_text({'@type': 'messageChecklist', 'list': {'title': {'text': 'Launch'},
            'tasks': [{'text': {'text': 'Configure SIP'}, 'completion_date': 42}]}})[0], 'Launch\nConfigure SIP')

    def test_title_is_case_and_space_insensitive(self) -> None:
        self.assertEqual(normalize_title("  КАРЬЕРНЫЙ   УЛЕЙ "), "карьерный улей")

    def test_approved_words_allow_emoji_and_suffix(self) -> None:
        self.assertTrue(approved_title_matches("Example Careers", "🐝 Example Careers | Вакансии"))
        self.assertFalse(approved_title_matches("Example Careers", "Карьерный клуб"))

    def test_text_message_is_normalized(self) -> None:
        message = normalize_message(
            {
                "chat_id": -1001,
                "id": 1048576,
                "date": 1785600000,
                "sender_id": {"@type": "messageSenderUser", "user_id": 42},
                "content": {
                    "@type": "messageText",
                    "text": {"@type": "formattedText", "text": "Need DevOps help"},
                },
                "reply_to": {
                    "@type": "messageReplyToMessage",
                    "chat_id": -1001,
                    "message_id": 524288,
                },
            }
        )
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.text, "Need DevOps help")
        self.assertEqual(message.reply_to_message_id, 524288)

    def test_hidden_text_url_and_visible_url_are_preserved(self) -> None:
        content = {
            "@type": "messageText",
            "text": {
                "@type": "formattedText",
                "text": "Full description and https://example.com/public.",
                "entities": [
                    {
                        "offset": 0,
                        "length": 16,
                        "type": {
                            "@type": "textEntityTypeTextUrl",
                            "url": "https://app.rvc.global/vacancy/view/test?a=1&amp;b=2",
                        },
                    }
                ],
            },
        }
        self.assertEqual(
            extract_embedded_urls(content),
            (
                "https://app.rvc.global/vacancy/view/test?a=1&b=2",
                "https://example.com/public",
            ),
        )


if __name__ == "__main__":
    unittest.main()
