from __future__ import annotations

import unittest

from telegram_project_radar.selection_v2 import (
    build_candidate_items,
    classify_message,
    evaluate_item,
    is_digest,
    is_structured_vacancy,
    needs_detail_enrichment,
    near_duplicate,
    split_digest,
)


class SelectionV2Tests(unittest.TestCase):
    def test_russian_cities_in_work_conditions_override_remote(self) -> None:
        for city in ("Калуга", "Орёл", "Нижний Новгород", "Тула", "Калуге", "Орле", "Нижнем Новгороде", "Туле", "Kaluga", "Oryol", "Nizhny Novgorod", "Tula"):
            for item_type in ("opportunity", "digest_item"):
                with self.subTest(city=city, item_type=item_type):
                    result = evaluate_item(
                        "DevOps engineer — contract hosting infrastructure remote",
                        item_type, f"Формат / location: {city}; remote",
                    )
                    self.assertEqual(result.decision, "exclude")
                    self.assertIn("российские города", result.reasons[0])

    def test_mixed_foreign_hybrid_remote_is_excluded(self) -> None:
        for detail in (
            "Формат: гибрид в городах Калуга, Орёл, Нижний Новгород, Тула / удалённо в других городах",
            "Work format: hybrid in Paris / remote in other cities",
            "Location: Berlin. Onsite and remote work",
        ):
            with self.subTest(detail=detail):
                result = evaluate_item("DevOps engineer contract remote", "opportunity", detail)
                self.assertEqual(result.decision, "exclude")

    def test_allowed_remote_spain_and_non_location_terms_are_preserved(self) -> None:
        for detail in (
            "Contract. Remote worldwide.",
            "Формат: гибрид в Мадриде / удалённо в других городах Испании",
            "Work format: hybrid in Madrid, Spain / remote",
            "Remote contract. Configure hybrid cloud infrastructure and Microsoft Office 365",
            "Удалённый проект: поддержка гибридного облака",
            "Наши клиенты в Туле. Формат: remote worldwide",
            "Remote contract in Tularosa, New Mexico",
            "Available formats\nOn-site\nRemote\nChoose remote for this contract.",
        ):
            with self.subTest(detail=detail):
                result = evaluate_item("DevOps engineer contract remote", "opportunity", detail)
                self.assertEqual(result.decision, "include")

    def test_russian_city_in_detail_also_excludes_company(self) -> None:
        items = build_candidate_items([{
            "chat_id": 1, "message_id": 50,
            "text": "DevOps Engineer\nExample Co | Hosting\nRemote\nSkills: linux",
            "detail_text": "Формат: Тула / удалённо в других городах",
        }])
        self.assertFalse(any(item.item_type == "company" for item in items))
        self.assertEqual(items[0].evaluation.decision, "exclude")

    def test_foreign_hybrid_vacancy_does_not_erase_remote_company_research(self) -> None:
        items = build_candidate_items([{
            "chat_id": 1, "message_id": 51,
            "text": "DevOps Engineer\nExample Co | Hosting\nRemote\nSkills: linux",
            "detail_text": "Work format: hybrid in Paris / remote elsewhere",
        }])
        self.assertEqual(next(i for i in items if i.item_type == "opportunity").evaluation.decision, "exclude")
        self.assertEqual(next(i for i in items if i.item_type == "company").evaluation.decision, "include")

    def test_legal_infrastructure_is_not_technical_infrastructure(self) -> None:
        result = evaluate_item(
            "Ищем CEO для развития юридической инфраструктуры компании",
            "opportunity",
        )
        self.assertEqual(result.decision, "exclude")

    def test_performance_marketing_person_is_irrelevant(self) -> None:
        result = evaluate_item(
            "Ищу новый проект. Опыт: Meta Ads, Google Ads, performance marketing и affiliate.",
            "person",
        )
        self.assertEqual(result.decision, "exclude")

    def test_provider_devops_person_is_separate_person_result(self) -> None:
        text = "Я DevOps и системный администратор, работал у интернет-провайдеров, открыт к IT-проектам."
        self.assertEqual(classify_message(text), "person")
        self.assertIn(evaluate_item(text, "person").decision, {"include", "review"})

    def test_promotional_crm_post_is_advertisement(self) -> None:
        text = "Наш оффер: мы помогаем настроить CRM и подобрать вакансии. Записывайтесь."
        self.assertEqual(classify_message(text), "advertisement")
        self.assertEqual(evaluate_item(text, "advertisement").decision, "exclude")

    def test_editorial_hr_offer_is_advertisement_even_with_hiring_word(self) -> None:
        text = "Почему карьерный HR-оффер работает: ищем правильный network и строим воронку"
        self.assertEqual(classify_message(text), "advertisement")

    def test_freelance_funnel_task_is_included(self) -> None:
        result = evaluate_item(
            "Нужен freelance специалист на проект: написать несколько воронок",
            "opportunity",
        )
        self.assertEqual(result.decision, "include")

    def test_b2b_saas_lead_generation_and_outbound_is_included(self) -> None:
        result = evaluate_item(
            "Ищем Senior SDR: Lead Generation для B2B SaaS и полный цикл outbound",
            "opportunity",
        )
        self.assertEqual(result.decision, "include")

    def test_salesforce_work_is_excluded_by_profile(self) -> None:
        result = evaluate_item(
            "Ищем Salesforce Administrator для автоматизации процессов",
            "opportunity",
        )
        self.assertEqual(result.decision, "exclude")

    def test_weak_cloud_digest_item_is_excluded(self) -> None:
        result = evaluate_item("Cloud specialist для клиентов", "digest_item")
        self.assertEqual(result.decision, "exclude")

    def test_technical_sales_digest_item_can_be_reviewed(self) -> None:
        result = evaluate_item(
            "Sales manager в B2B software компании",
            "digest_item",
        )
        self.assertEqual(result.decision, "review")

    def test_cloud_sector_vacancy_link_is_relevant(self) -> None:
        result = evaluate_item(
            "Ищем Incident Response Lead: https://www.jettycloud.com/vacancies/role",
            "opportunity",
        )
        self.assertEqual(result.decision, "include")

    def test_structured_job_card_without_hiring_word_is_an_opportunity(self) -> None:
        text = (
            "Senior Azure DevOps Engineer\n"
            "Company | IT services\n"
            "Требуемые языки: English\n"
            "Skills: azure, devops, terraform"
        )
        self.assertEqual(classify_message(text), "opportunity")

    def test_structured_developer_is_not_selected_for_devops_skill_only(self) -> None:
        text = (
            "Senior Java Developer\n"
            "Company | Financial technology\n"
            "Требуемые языки: English\n"
            "Skills: java, docker, devops, aws"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_telecom_industry_alone_does_not_select_unrelated_role(self) -> None:
        text = (
            "Data Engineer\n"
            "Telecommunications\n"
            "Belarus\n"
            "Удаленка\n"
            "Требуемые языки: Russian\n"
            "Skills: clickhouse, pandas"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_unknown_structured_role_is_not_selected_by_telecom_industry_alone(self) -> None:
        text = (
            "UX Writer\n"
            "Company | Telecommunications\n"
            "Worldwide Remote\n"
            "Required languages: English\n"
            "Skills: writing, research"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_known_structured_role_without_skills_is_still_recognized(self) -> None:
        text = (
            "AI Infrastructure Cloud Engineer\n"
            "EWOR | Startup incubation and venture building\n"
            "Worldwide Remote\n"
            "Required languages: English"
        )
        self.assertTrue(is_structured_vacancy(text))

    def test_full_time_in_linked_detail_excludes_devops_card(self) -> None:
        text = (
            "DevOps Engineer\n"
            "Company | B2B SaaS\n"
            "Worldwide Remote\n"
            "Required languages: English\n"
            "Skills: linux, terraform"
        )
        result = evaluate_item(
            text,
            "opportunity",
            "Employment type: Full Time. Build reliable cloud infrastructure.",
        )
        self.assertEqual(result.decision, "exclude")
        self.assertIn("полном описании", result.reasons[0])

    def test_linked_detail_cannot_turn_foreign_office_card_into_remote(self) -> None:
        text = (
            "DevOps Engineer\n"
            "Swisscom | Telecommunications\n"
            "Netherlands | Rotterdam\n"
            "Office | Relocation available\n"
            "Required languages: English\n"
            "Skills: kubernetes"
        )
        result = evaluate_item(
            text,
            "opportunity",
            "The careers site navigation also lists remote jobs and hybrid jobs.",
        )
        self.assertEqual(result.decision, "exclude")
        self.assertIn("Onsite/office", result.reasons[0])

    def test_telecom_card_can_use_concrete_task_from_linked_detail(self) -> None:
        text = (
            "Technical Coordinator\n"
            "Company | Telecommunications\n"
            "Worldwide Remote\n"
            "Required languages: English\n"
            "Skills: coordination"
        )
        result = evaluate_item(
            text,
            "opportunity",
            "Contract role configuring Linux servers, VPN and network monitoring.",
        )
        self.assertEqual(result.decision, "include")
        self.assertIn(
            "Полное описание по прямой ссылке проверено", result.reasons
        )

    def test_profile_and_priority_sector_cards_request_linked_detail(self) -> None:
        devops = (
            "DevOps Engineer\nCompany | B2B SaaS\nWorldwide Remote\n"
            "Required languages: English\nSkills: terraform"
        )
        telecom_designer = (
            "UI/UX Designer\nCompany | Telecommunications\nWorldwide Remote\n"
            "Required languages: English\nSkills: figma"
        )
        unrelated = (
            "UI/UX Designer\nCompany | Retail\nWorldwide Remote\n"
            "Required languages: English\nSkills: figma"
        )
        self.assertTrue(needs_detail_enrichment(devops))
        self.assertTrue(needs_detail_enrichment(telecom_designer))
        self.assertFalse(needs_detail_enrichment(unrelated))

    def test_infrastructure_title_remains_selected_without_sector_label(self) -> None:
        text = (
            "Infrastructure Engineer\n"
            "Company | Artificial Intelligence\n"
            "Remote\n"
            "Required languages: English\n"
            "Skills: linux"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "include")

    def test_mts_opportunity_and_company_are_excluded(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 50,
                "text": (
                    "Senior Java Developer\n"
                    "МТС Web Services | Telecommunications\n"
                    "Удаленка\n"
                    "Требуемые языки: Russian\n"
                    "Skills: java, networking"
                ),
            }
        ]
        items = build_candidate_items(rows)
        opportunity = next(item for item in items if item.item_type == "opportunity")
        self.assertEqual(opportunity.evaluation.decision, "exclude")
        self.assertNotIn("company", {item.item_type for item in items})

    def test_structured_devops_title_is_selected(self) -> None:
        text = (
            "Senior DevOps Engineer\n"
            "Company | Professional services\n"
            "Требуемые языки: English\n"
            "Skills: terraform, aws"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "include")

    def test_structured_lead_generation_in_saas_is_selected(self) -> None:
        text = (
            "Lead Generation Manager\n"
            "Company | Marketing analytics SaaS\n"
            "Требуемые языки: English\n"
            "Skills: outbound, crm"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "include")

    def test_foreign_office_role_is_excluded(self) -> None:
        text = (
            "Senior DevOps Engineer\n"
            "Company | IT services\n"
            "India | Noida\n"
            "Офис\n"
            "Требуемые языки: English\n"
            "Skills: terraform, aws"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_remote_russia_role_is_excluded(self) -> None:
        text = "DevOps Engineer\nCompany | Hosting\nRussia\nУдаленка\nSkills: linux"
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_remote_moscow_role_is_excluded(self) -> None:
        text = "Golang Developer\nVolta | Cloud infrastructure\nMoscow\nУдаленка\nSkills: go"
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_remote_india_role_is_excluded(self) -> None:
        text = "DevOps Engineer\nLuxoft | IT services\nIndia\nRemote\nSkills: linux"
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_rub_salary_is_excluded(self) -> None:
        text = "DevOps Engineer\nCompany | Hosting\n200 000 RUB в месяц\nУдаленка"
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_san_francisco_vacancy_is_excluded(self) -> None:
        text = "Infrastructure Engineer\nCompany | AI\nSan Francisco\nRemote"
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_mlops_role_is_excluded(self) -> None:
        text = "MLOps Engineer\nCompany | SaaS\nRemote"
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_staff_and_cto_roles_are_excluded(self) -> None:
        for title in ("Staff DevOps Engineer", "CTO"):
            with self.subTest(title=title):
                text = f"{title}\nCompany | Software\nRemote\nSkills: linux"
                self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_sre_and_network_engineer_are_selected(self) -> None:
        for title in ("Site Reliability Engineer", "Network Engineer"):
            with self.subTest(title=title):
                text = f"{title}\nCompany | Technology\nRemote\nSkills: linux"
                self.assertEqual(evaluate_item(text, "opportunity").decision, "include")

    def test_spanish_office_role_is_not_excluded_by_geography(self) -> None:
        text = (
            "Senior DevOps Engineer\n"
            "Company | IT services\n"
            "Spain | Madrid\n"
            "Офис\n"
            "Требуемые языки: English\n"
            "Skills: terraform, aws"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "include")

    def test_azure_focused_role_is_excluded(self) -> None:
        text = (
            "Senior Azure DevOps Engineer\n"
            "Company | IT services\n"
            "Удаленка\n"
            "Требуемые языки: English\n"
            "Skills: terraform"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_azure_in_skill_list_is_also_excluded(self) -> None:
        text = (
            "Site Reliability Engineer\n"
            "Company | Gaming\n"
            "Удаленка\n"
            "Требуемые языки: English\n"
            "Skills: aws, azure, terraform"
        )
        self.assertEqual(evaluate_item(text, "opportunity").decision, "exclude")

    def test_azure_in_broad_person_experience_does_not_hide_person(self) -> None:
        text = (
            "Я DevOps и системный администратор, работал у провайдеров с Azure и Linux, "
            "открыт к инфраструктурным IT-проектам"
        )
        self.assertIn(evaluate_item(text, "person").decision, {"include", "review"})

    def test_remote_tech_company_hiring_infrastructure_gets_company_item(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 2,
                "text": (
                    "IT Monitoring Engineer\n"
                    "JetBrains | Software and technology\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: prometheus"
                ),
            }
        ]
        items = build_candidate_items(rows)
        self.assertIn("company", {item.item_type for item in items})

    def test_named_belarus_tech_company_gets_company_item(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 3,
                "text": (
                    "Lead Generation Manager\n"
                    "Campaignswell | Marketing analytics SaaS\n"
                    "Belarus\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: outbound"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0].evaluation.decision, "include")

    def test_belarus_alone_does_not_create_company(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 30,
                "text": (
                    "Lead Product Manager\n"
                    "Wellbeing Co | Health & Fitness\n"
                    "Belarus\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: product management"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_generic_belarus_software_company_is_not_added_automatically(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 301,
                "text": (
                    "Backend Developer\n"
                    "Ordinary Co | Software development\n"
                    "Belarus\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: python"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_company_requires_explicit_name_separator(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 31,
                "text": (
                    "Data Engineer\n"
                    "Telecommunications\n"
                    "Belarus\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: networking"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_network_signal_creates_top_research_company(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 32,
                "text": (
                    "Senior DevOps Engineer\n"
                    "Foxford | Education\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: linux, networking"
                ),
            }
        ]
        items = build_candidate_items(rows)
        opportunity = next(item for item in items if item.item_type == "opportunity")
        company = next(item for item in items if item.item_type == "company")
        self.assertEqual(opportunity.evaluation.decision, "exclude")
        self.assertEqual(company.evaluation.decision, "include")
        self.assertEqual(company.evaluation.score, 90)
        self.assertIn("Приоритет: top research", company.text)

    def test_network_skill_alone_does_not_create_company_for_unrelated_role(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 320,
                "text": (
                    "Search Engineer\n"
                    "EasySoftGroup | Information technology and software consulting\n"
                    "United States of America\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: golang, networking, typescript"
                ),
            },
            {
                "chat_id": 1,
                "message_id": 321,
                "text": (
                    "QA Automation Engineer\n"
                    "Luxoft | Financial technology\n"
                    "Ukraine\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: python, tcp_ip, udp"
                ),
            },
            {
                "chat_id": 1,
                "message_id": 322,
                "text": (
                    "Principal Governance, Risk and Compliance Architect\n"
                    "SimScale GmbH | Technology, Information and Internet\n"
                    "Germany\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: aws, gdpr, network_security"
                ),
            },
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_san_francisco_company_is_not_created(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 323,
                "text": (
                    "Member of Technical Staff, Cluster Administration\n"
                    "Inferact | Artificial intelligence infrastructure\n"
                    "United States of America | San Francisco\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: networking, terraform, vpn"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_vpn_engineer_is_a_company_signal_without_reclassifying_the_vacancy(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 324,
                "text": (
                    "VPN Engineer\n"
                    "TrueSpace | Technology\n"
                    "Удаленка\n"
                    "Требуемые языки: Russian\n"
                    "Skills: wireguard, dns, linux"
                ),
            }
        ]
        items = build_candidate_items(rows)
        opportunity = next(item for item in items if item.item_type == "opportunity")
        company = next(item for item in items if item.item_type == "company")
        self.assertEqual(opportunity.evaluation.decision, "exclude")
        self.assertEqual(company.evaluation.decision, "include")

    def test_generic_gtm_recruiter_and_nontechnical_market_research_are_not_company_signals(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 325,
                "text": (
                    "Senior Recruiter | GTM Expansion\n"
                    "Ramp | Financial technology\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: market_research"
                ),
            },
            {
                "chat_id": 1,
                "message_id": 326,
                "text": (
                    "Lead Market Researcher\n"
                    "Monzo | Banking and financial technology\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: data, market_research"
                ),
            },
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_technical_gtm_platform_role_is_a_company_signal(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 327,
                "text": (
                    "Lead GTM Platform Engineer\n"
                    "Skydio | Aerospace and Defense Technology\n"
                    "United States of America | San Mateo\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: data, llm, system_design"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(len(companies), 1)

    def test_mandatory_relocation_outside_spain_excludes_company(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 328,
                "text": (
                    "Infrastructure Engineer\n"
                    "Aleria LLC | Artificial Intelligence\n"
                    "United Arab Emirates | Abu Dhabi\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: kubernetes, vpn"
                ),
                "detail_text": "Удалённо два месяца, затем обязателен релок в Абу Даби",
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_russian_inflection_in_detail_excludes_company(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 329,
                "text": (
                    "Python Backend Developer\n"
                    "Cloud.ru | Cloud computing\n"
                    "Удаленка\n"
                    "Требуемые языки: Russian\n"
                    "Skills: python"
                ),
                "detail_text": "Компания входит в число крупнейших ИТ-компаний России",
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_data_posture_role_is_company_only(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 33,
                "text": (
                    "Data Posture Monitoring Manager\n"
                    "Cummins Inc. | Industrial machinery\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: information_security"
                ),
            }
        ]
        items = build_candidate_items(rows)
        opportunity = next(item for item in items if item.item_type == "opportunity")
        company = next(item for item in items if item.item_type == "company")
        self.assertEqual(opportunity.evaluation.decision, "exclude")
        self.assertEqual(company.evaluation.decision, "include")

    def test_confirmed_gaming_company_gets_future_infinity_route(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 34,
                "text": (
                    "Senior DevOps Engineer\n"
                    "Aya Games | Gaming\n"
                    "Удаленка\n"
                    "Требуемые языки: English\n"
                    "Skills: linux"
                ),
            }
        ]
        company = next(
            item for item in build_candidate_items(rows) if item.item_type == "company"
        )
        self.assertIn("Будущий маршрут: Infinity", company.text)

    def test_foreign_onsite_company_is_not_created(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 35,
                "text": (
                    "Implementation and Support Engineer\n"
                    "Fintech Co | Financial technology\n"
                    "Belarus | Minsk\n"
                    "Офис\n"
                    "Требуемые языки: Russian\n"
                    "Skills: vpn"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_cyrillic_and_latin_mts_company_names_are_both_excluded(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": message_id,
                "text": (
                    f"Software Engineer\n{name} | Telecommunications\n"
                    "Удаленка\nТребуемые языки: English\nSkills: python"
                ),
            }
            for message_id, name in ((40, "MTS Web Services"), (41, "МТС Web Services"))
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_russia_company_is_not_created(self) -> None:
        rows = [
            {
                "chat_id": 1,
                "message_id": 4,
                "text": (
                    "DevOps Engineer\n"
                    "Company | Software development\n"
                    "Russia\n"
                    "Удаленка\n"
                    "Требуемые языки: Russian\n"
                    "Skills: linux"
                ),
            }
        ]
        companies = [item for item in build_candidate_items(rows) if item.item_type == "company"]
        self.assertEqual(companies, [])

    def test_digest_is_split_into_blocks(self) -> None:
        blocks = ["Еженедельная подборка вакансий"]
        blocks.extend(
            f"DevOps engineer {index} — Kubernetes and hosting\n"
            f"Описание позиции {index}\nУсловия проекта {index}"
            for index in range(30)
        )
        text = "\n\n".join(blocks)
        self.assertTrue(is_digest(text))
        self.assertGreaterEqual(len(split_digest(text)), 2)

    def test_near_duplicate_detects_short_digest_version(self) -> None:
        standalone = "Ищем DevOps engineer для Kubernetes, Linux и hosting infrastructure на контракт"
        digest = "DevOps engineer — Kubernetes Linux hosting infrastructure, contract"
        self.assertTrue(near_duplicate(standalone, digest))


if __name__ == "__main__":
    unittest.main()
