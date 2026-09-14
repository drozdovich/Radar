# Hackathon application draft

This is a general draft. The exact event requirements and time limit have not yet been supplied.

## Short motivation — Russian

Я разрабатываю Radar — инструмент, который помогает находить проекты и полезные контакты в Telegram. Он сохраняет исходное сообщение и точную цитату, объясняет возможную пользу и передаёт находку на проверку человеку в CRM. У меня уже есть рабочий прототип на Python и TypeScript, тесты и демонстрация без доступа к личным данным. Разрабатываю с помощью Codex и на практике учусь собирать проверяемую систему, а не только отдельные скрипты. На хакатоне хочу довести один полный сценарий — от сообщения до правильно связанной карточки — до воспроизводимой установки и проверить качество отбора на новых примерах.

## Short motivation — English

I am building Radar, a tool that turns selected Telegram conversations into project opportunities and useful contacts for human review. It keeps the original message, exact evidence and unknowns, then connects approved findings to a CRM. I already have a Python and TypeScript prototype, automated checks and a demo that runs without private data. I use Codex in development and am learning to build a system whose behaviour can be verified. At the hackathon, I want to make one complete message-to-reviewed-record workflow reproducible and evaluate the quality of selection on fresh examples.

## A bounded project plan

- Make the existing review workflow reproducible in an isolated environment.
- Verify stable identities, retained source evidence and correct CRM links.
- Evaluate a small fresh, separately labelled set; report both misses and false positives.
- Present a working example and the remaining limitations.

The source repository and synthetic demo support the implementation claims. They do not support claims of production readiness, perfect AI accuracy or independently verified business results.
