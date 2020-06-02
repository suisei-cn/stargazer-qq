import asyncio
import json
import logging
import os
import time
from asyncio import Queue
from socket import gaierror
from typing import Optional
from urllib.parse import urljoin

import httpx
import nonebot
import websockets
from aiocqhttp import Event as CQEvent
from nonebot import CommandSession, RequestSession, on_command, on_request, scheduler
from websockets.exceptions import ConnectionClosedError

WORKERS = int(os.environ.get("WORKERS", "10"))
M2M_TOKEN = os.environ["M2M_TOKEN"]
BACKEND_URL = os.environ["BACKEND_URL"]
FRONTEND_URL = os.environ["FRONTEND_URL"]
MESSAGE_WS = os.environ["MESSAGE_WS"]

http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {M2M_TOKEN}"})
bot = nonebot.get_bot()
help_msg = "\n".join([
    "PyStargazer QQ Wrapper",
    "/register - Register account",
    "/settings - Set preference",
    "/delete_account - Delete account",
    "Only group owner/admins can send commands to me if I'm in a group",
    "In this case settings link will be sent to the sender privately."
])


async def dispatch(topic: str, event_type: str, msg: str):
    def _parse(user_string: str) -> Optional[str]:
        try:
            user_type, user_id = user_string.split("+")
        except ValueError:
            return
        if user_type != "qq":
            return
        return user_id

    async def send_msg(user_id_str: str, msg: str):
        try:
            user_type, user_id = user_id_str.split("_")
        except ValueError:
            return
        if user_type == "group":
            await bot.send_group_msg(group_id=user_id, message=msg)
        elif user_type == "private":
            await bot.send_private_msg(user_id=user_id, message=msg)
        elif user_type == "discuss":
            await bot.send_discuss_msg(discuss_id=user_id, message=msg)

    logging.info(f"Dispatcher: Incoming {event_type} event.")
    all_users = (await http_client.get(urljoin(BACKEND_URL, f"m2m/subs/{topic}"), params={"type": event_type})).json()
    users = [user for _user in all_users if (user := _parse(_user))]
    logging.info(f"Dispatcher: Sending to users {users}")
    await asyncio.gather(*(send_msg(user, msg) for user in users))


def build_message(msg: dict) -> str:
    rtn = [f'【{msg["name"]}】{msg["title"]}',
           '————————————',
           msg["text"]]
    for image in msg.get("images", []):
        rtn.append(f"[CQ:image,file={image}]")
    return "\n".join(rtn)


event_map = {
    "t_tweet": "Twitter 推文",
    "t_rt": "Twitter 转推",
    "bili_plain_dyn": "Bilibili 动态",
    "bili_rt_dyn": "Bilibili 转发",
    "bili_img_dyn": "Bilibili 图片动态",
    "bili_video": "Bilibili 视频",
    "ytb_video": "Youtube 视频",
    "ytb_reminder": "Youtube 配信提醒",
    "ytb_live": "Youtube 上播",
    "ytb_sched": "Youtube 新配信计划"
}


def decode_event(event: dict) -> Optional[dict]:
    try:
        msg: dict = {"name": event["vtuber"], "images": event["data"].get("images", []),
                     "title": event_map.get(event["type"], event["type"])}

        msg_body = []
        if body := event["data"].get("title"):
            msg_body.append(body)
        if body := event["data"].get("text"):
            msg_body.append(body)
        if sched_time := event["data"].get("scheduled_start_time"):
            msg_body.append(f"预定时间：{sched_time}")
        if actual_time := event["data"].get("actual_start_time"):
            msg_body.append(f"上播时间：{actual_time}")
        if link := event["data"].get("link"):
            msg_body.append(f"链接：{link}")
        msg["text"] = "\n".join(msg_body)
        logging.info(f"debug: {msg}")

        if msg.get("title"):
            return msg
        else:
            return None
    except Exception as e:
        logging.info(str(e))


async def worker(queue: Queue):
    logging.debug("worker up")
    while True:
        raw_event = await queue.get()
        logging.info(f"got raw_event {raw_event}")
        event = json.loads(raw_event)
        logging.info(f"decoded into dict {event}")
        topic = event["vtuber"]
        event_type = event["type"]
        msg = decode_event(event)
        logging.info(f"decoded into msg {msg}")
        msg_cq = build_message(msg)
        logging.info(f"encoded into string {msg_cq}")

        if msg_cq:
            logging.info(f"sending to dispatcher {topic} {event_type} {msg_cq}")
            await dispatch(topic, event_type, msg_cq)

        queue.task_done()


@scheduler.scheduled_job(None)
async def event_routine():
    task_queue = Queue()
    tasks = []
    for i in range(WORKERS):
        task = asyncio.create_task(worker(task_queue))
        tasks.append(task)

    logging.debug("event loop up")
    while True:
        try:
            async with websockets.connect(MESSAGE_WS) as ws:
                async for raw_event in ws:
                    logging.info(f"incoming event {raw_event}")
                    task_queue.put_nowait(raw_event)
        except (ConnectionClosedError, gaierror, ConnectionRefusedError):
            time.sleep(5)


def get_user_string(ctx: CQEvent):
    def get_user(ctx: CQEvent):
        user_type = ctx.detail_type
        if user_type == "private":
            user_id = ctx.user_id
        elif user_type == "group":
            user_id = ctx.group_id
        elif user_type == "discuss":
            user_id = ctx.discuss_id
        else:
            user_id = ""
        return user_type, user_id

    user_type, user_id = get_user(ctx)
    return f"qq+{user_type}_{user_id}"


def get_privileged_user(ctx: CQEvent) -> Optional[int]:
    if ctx.detail_type == "group" and ctx.sender.get("role") not in ["owner", "admin"]:
        return
    return ctx.user_id


@on_request
async def approve_request(session: RequestSession):
    ctx: CQEvent = session.ctx
    if ctx.detail_type == "friend":
        user_id = session.ctx.user_id
        await session.approve()
        await bot.send_private_msg(user_id=user_id, message=help_msg)
        logging.info(f"New user approved: {user_id}")
    elif ctx.detail_type == "group" and ctx.sub_type == "invite":
        group_id = session.ctx.group_id
        await session.approve()
        await bot.send_group_msg(group_id=group_id, message=help_msg)
        logging.info(f"New group approved: {group_id}")


@on_command("register")
async def register_user(session: CommandSession):
    if not get_privileged_user(session.ctx):
        return
    user_string = get_user_string(session.ctx)
    r = await http_client.post(urljoin(BACKEND_URL, "users"), data=user_string)
    if r.status_code == 409:
        await session.send(f"Account already exists. Please use command /settings to set your preference.")
    elif r.status_code == 204:
        await session.send("Account created. Please use command /settings to set your preference.")
    else:
        await session.send(f"{r.status_code} {r.text}")


@on_command("delete_account")
async def delete_account(session: CommandSession):
    if not get_privileged_user(session.ctx):
        return
    user_string = get_user_string(session.ctx)
    for_sure = session.current_arg_text.strip() == "!force"
    if not for_sure:
        await session.send(
            f"You are going to delete your account.\n"
            f"Your account and data will be removed from the database immediately!\n"
            f"Please confirm your request by sending /delete_account !force")
        return

    r = await http_client.delete(urljoin(BACKEND_URL, f"users/{user_string}"))
    if r.status_code == 204:
        await session.send("Account deleted.")
    else:
        await session.send(f"{r.status_code} {r.text}")


@on_command("settings")
async def get_settings_url(session: CommandSession):
    user_string = get_user_string(session.ctx)
    if not (privileged_user := get_privileged_user(session.ctx)):
        return

    r = await http_client.get(urljoin(BACKEND_URL, f"m2m/get_token/{user_string}"))
    if r.status_code == 200:
        token = r.text
        url = urljoin(FRONTEND_URL, f"auth?token={token}")
        await bot.send_private_msg(user_id=privileged_user,
                                   message=f"Please click the link below to set your preference.\n"
                                           f"The link will expire in 10 minutes.\n{url}")
    elif r.status_code == 404:
        await bot.send_private_msg(user_id=privileged_user,
                                   message="User doesn't exist. Please first register by command /register.")
    else:
        await bot.send_private_msg(user_id = privileged_user, message="{r.status_code} {r.text}")


@on_command("help")
async def get_help(session: CommandSession):
    if not get_privileged_user(session.ctx):
        return
    await session.send(help_msg)
