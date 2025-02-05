import asyncio
import json

import logging
import re

from openai import OpenAI, OpenAIError, BadRequestError

from openai.types.beta.threads.run_submit_tool_outputs_params import ToolOutput

import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

client = OpenAI(api_key=OPENAI_KEY)

async def create_thread(question):
    thread = client.beta.threads.create(
        messages=[
            {
                "role": "user",
                "content": question,
            }
        ],
    )
    return thread.id

async def add_user_message(thread_id, user_message):
    max_attempts = 5
    attempt = 0

    async def check_active_runs(thread_id):
        while True:
            runs = client.beta.threads.runs.list(thread_id=thread_id)
            active_runs = [run for run in runs if run.status == 'in_progress']
            if not active_runs:
                return True
            logging.info("Ожидание завершения всех активных процессов...")
            await asyncio.sleep(1)

    try:
        while True:
            runs = client.beta.threads.runs.list(thread_id=thread_id)
            active_runs = [run for run in runs if run.status == 'in_progress']
            if not active_runs:
                break
            logging.info("Активный процесс найден, ожидание завершения...")
            await asyncio.sleep(1)

        while attempt < max_attempts:
            try:
                client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=user_message
                )
                logging.info("Сообщение успешно отправлено.")
                return
            except BadRequestError as e:
                if "Can't add messages to" in str(e):
                    attempt += 1
                    logging.warning(f"Процесс активен, повторная попытка через 1 секунду... ({attempt}/{max_attempts})")
                    await asyncio.sleep(1)
                else:
                    logging.error(f"Ошибка при отправке сообщения в поток: {e}")
                    raise

        if attempt >= max_attempts:
            logging.warning("Достигнут лимит попыток, завершаем активный процесс.")
            tool_outputs = []
            for run in active_runs:
                tool_outputs.append(ToolOutput(
                    tool_call_id=run.id,
                    output=json.dumps({"result": "success"})
                ))
                try:
                    client.beta.threads.runs.submit_tool_outputs(
                        thread_id=thread_id,
                        run_id=run.id,
                        tool_outputs=tool_outputs
                    )
                    logging.info(f"Процесс {run.id} успешно завершен.")
                except BadRequestError as e:
                    logging.error(f"Ошибка при завершении процесса {run.id}: {e}")
                    raise

            if await check_active_runs(thread_id):
                try:
                    client.beta.threads.messages.create(
                        thread_id=thread_id,
                        role="user",
                        content=user_message
                    )
                    logging.info("Сообщение успешно отправлено после завершения активного процесса.")
                except BadRequestError as e:
                    logging.error(f"Ошибка при повторной отправке сообщения в поток: {e}")
                    raise
    except OpenAIError as e:
        logging.error(f"Ошибка OpenAI: {e}")
        raise
    except Exception as e:
        logging.error(f"Неожиданная ошибка: {e}")
        raise

async def create_run(thread_id):
    try:
        run = client.beta.threads.runs.create_and_poll(
            thread_id=thread_id, assistant_id=ASSISTANT_ID, tool_choice={"type": "file_search"},
        )

        messages_page = client.beta.threads.messages.list(
            thread_id=thread_id, run_id=run.id
        )

        response_text = ""

        messages_list = [await message_to_dict(message) for message in messages_page]
        for message in messages_list:
            response_text = message['content'][0]['text']
            logging.info(f"Assistant response: {response_text}")

        text = await remove_square_brackets(response_text)

        return text

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        raise

async def remove_square_brackets(content):
    cleaned_content = re.sub(r'【[^】]+】', '', content)
    return cleaned_content


async def message_to_dict(message):
    return {
        'id': message.id,
        'assistant_id': message.assistant_id,
        'completed_at': message.completed_at,
        'content': [{'text': content_block.text.value, 'type': content_block.type} for content_block in
                    message.content],
        'created_at': message.created_at,
        'incomplete_at': message.incomplete_at,
        'metadata': message.metadata,
        'object': message.object,
        'role': message.role,
        'run_id': message.run_id,
        'status': message.status,
        'thread_id': message.thread_id
    }