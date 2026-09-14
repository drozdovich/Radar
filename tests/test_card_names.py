import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from telegram_project_radar.card_names import apply_author_name, author_name, needs_author_name
from telegram_project_radar.tdlib_source import TdlibSource


class CardNameTests(unittest.TestCase):
    def test_full_name_nickname_and_self_introduction_have_safe_fallbacks(self):
        self.assertEqual(author_name({'first_name': 'Oleksandr', 'last_name': 'Korsikov'}, 'Я Александр.'), 'Oleksandr Korsikov')
        self.assertEqual(author_name({'first_name': '', 'usernames': ['project_author']}, ''), '@project_author')
        self.assertEqual(author_name({'first_name': 'A', 'last_name': 'Z'}, 'Всем привет! Я Андрей, DevOps.'), 'Андрей')
        self.assertEqual(author_name(None, 'Меня зовут Павел. Я пока не живу здесь.'), 'Павел')
        self.assertEqual(author_name(None, 'Добрый день. Я пока не готов.'), 'Автор не указан')

    def test_vacancy_and_company_titles_stay_as_titles(self):
        for kind, text in [('company', 'Всем привет!'), ('digest_item', 'Всем привет!'),
                           ('opportunity', 'DevOps Engineer\nExample | Hosting')]:
            self.assertFalse(needs_author_name(kind, text))
        self.assertTrue(needs_author_name('person', '#whois\nМеня зовут Павел'))
        self.assertTrue(needs_author_name('opportunity', 'Всем привет!\nИщу партнёров.'))

    def test_renaming_preserves_source_and_status_and_does_not_invent_surname(self):
        item = {'type': 'person', 'summary': 'Всем привет! Я Андрей.', 'title': 'Человек: DevOps',
                'review_status': 'APPROVE', 'known_conditions': [], 'unknowns': []}
        apply_author_name(item, {'first_name': 'A', 'last_name': 'Z'})
        self.assertEqual(item['title'], 'Андрей')
        self.assertEqual(item['summary'], 'Всем привет! Я Андрей.')
        self.assertEqual(item['review_status'], 'APPROVE')
        self.assertIn('Полная фамилия автора в Telegram не указана.', item['unknowns'])

    def test_tdlib_reads_only_display_identity_not_phone_or_private_profile(self):
        tg = object.__new__(TdlibSource)
        tg._TdApi = SimpleNamespace(get_message=lambda chat, message: {'@type':'getMessage', 'chat_id':chat, 'message_id':message})
        tg._request = Mock(side_effect=[{'chat_id': 1, 'id': 2, 'sender_id': {'@type': 'messageSenderUser', 'user_id': 3}},
                                       {'id': 3, 'first_name': 'Pavel', 'last_name': '', 'phone_number': 'private', 'status': {'private': True}}])
        self.assertEqual(tg.get_message_author(1, 2), {'user_id': 3, 'first_name': 'Pavel', 'last_name': '', 'usernames': []})
        tg._request = Mock(return_value={'chat_id': 1, 'id': 2, 'forward_info': {'origin': 'someone'},
                                        'sender_id': {'@type': 'messageSenderUser', 'user_id': 3}})
        self.assertIsNone(tg.get_message_author(1, 2))
        self.assertEqual(tg._request.call_count, 1)


if __name__ == '__main__':
    unittest.main()


def test_person_profile_keeps_title_out_and_removes_promotional_surname():
    from telegram_project_radar.card_names import person_profile
    p = person_profile('radar-x', {'first_name':'Sviatoslav', 'last_name':'Dvoretskii'}, 'Anysite — данные B2B', 'https://t.me/x/1')
    assert (p['first_name'], p['last_name']) == ('Sviatoslav', 'Dvoretskii')
    p = person_profile('radar-x', {'first_name':'Alex Benkendorf', 'last_name':'Benkendorf.io'}, '', 'https://t.me/x/1')
    assert (p['first_name'], p['last_name']) == ('Alex Benkendorf', '')
    p = person_profile('radar-x', {'first_name':'Paul | Partnerships 🦅'}, '', 'https://t.me/x/1')
    assert (p['first_name'], p['last_name']) == ('Paul', '')
    assert person_profile('radar-x', None, 'Компания делает B2B', 'https://t.me/x/1')['status'] == 'unknown'
