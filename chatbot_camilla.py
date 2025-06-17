# -*- coding: utf-8 -*-

import os.path
import datetime as dt
import pytz
import requests
import json
import threading
import time
from flask import Flask, request, jsonify
from uuid import uuid4

# --- Dependências do Google Calendar ---
# pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- Configurações ---

# Configurações da Evolution API
EVOLUTION_API_URL = "http://127.0.0.1:8080"
EVOLUTION_API_KEY = "03qpt77ggm2pg06vc2vyxv"
INSTANCE_NAME = "agendacamilla"

# Configurações do Google Calendar e Timeout
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = 'primary'
TIMEZONE = 'America/Sao_Paulo'
INACTIVITY_TIMEOUT_SECONDS = 90  # 1 minuto e meio
FINAL_TIMEOUT_SECONDS = 30     # 30 segundos adicionais
ADDRESS = "Praça Rui Barbosa, n° 03, Centro, Maraú-BA"

# --- Inicialização do Flask ---
app = Flask(__name__)

# --- Variáveis Globais ---
conversation_state = {}

# --- Funções do Google Calendar ---


def get_calendar_service():
    """Autentica e retorna um objeto de serviço do Google Calendar."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    try:
        service = build("calendar", "v3", credentials=creds)
        return service
    except HttpError as error:
        print(f"Ocorreu um erro ao criar o serviço do Calendar: {error}")
        return None


def get_available_slots():
    """Verifica a agenda e retorna um dicionário de dias e horários disponíveis."""
    service = get_calendar_service()
    if not service:
        return {}

    local_tz = pytz.timezone(TIMEZONE)
    working_hours = ["09:00", "10:30", "15:00", "16:30"]
    now_utc = dt.datetime.now(pytz.utc)
    time_min = now_utc.isoformat()
    time_max = (now_utc + dt.timedelta(days=30)).isoformat()

    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID, timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy='startTime'
        ).execute()
        existing_events = events_result.get('items', [])
    except HttpError as error:
        print(f"Ocorreu um erro ao buscar eventos: {error}")
        return {}

    busy_slots = [
        (dt.datetime.fromisoformat(event['start'].get('dateTime')).astimezone(local_tz),
         dt.datetime.fromisoformat(event['end'].get('dateTime')).astimezone(local_tz))
        for event in existing_events if 'dateTime' in event['start'] and 'dateTime' in event['end']
    ]

    available_slots = {}
    today = dt.datetime.now(local_tz).date()

    for day_offset in range(30):
        current_day = today + dt.timedelta(days=day_offset)
        if current_day.weekday() not in [0, 1, 3]:
            continue  # Segunda, Terça, Quinta

        date_key = current_day.strftime("%d/%m/%Y")
        day_slots = []
        now_time = dt.datetime.now(local_tz)

        for time_str in working_hours:
            hour, minute = map(int, time_str.split(':'))
            potential_start = local_tz.localize(
                dt.datetime.combine(current_day, dt.time(hour, minute)))
            if potential_start < now_time:
                continue

            potential_end = potential_start + dt.timedelta(hours=1)
            is_busy = any(max(potential_start, bs) < min(
                potential_end, be) for bs, be in busy_slots)
            if not is_busy:
                day_slots.append(time_str)

        if day_slots:
            available_slots[date_key] = day_slots

    return available_slots


def create_calendar_event(summary, start_time, end_time, user_phone, appointment_type):
    """Cria um evento na agenda, adicionando um link do Meet se for remoto."""
    service = get_calendar_service()
    if not service:
        return None

    description = f'Tipo de Atendimento: {appointment_type.capitalize()}\nAgendamento solicitado por: {user_phone}'
    location = ADDRESS if appointment_type == 'presencial' else None

    event_body = {
        'summary': summary,
        'description': description,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': TIMEZONE},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': TIMEZONE},
    }

    if location:
        event_body['location'] = location

    if appointment_type.lower() == 'remoto':
        event_body['conferenceData'] = {
            'createRequest': {'requestId': str(uuid4()), 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}
        }

    try:
        created_event = service.events().insert(
            calendarId=CALENDAR_ID, body=event_body, conferenceDataVersion=1).execute()
        print(f"Evento criado: {created_event.get('htmlLink')}")
        return created_event
    except HttpError as error:
        print(f"Ocorreu um erro ao criar o evento: {error}")
        return None

# --- Funções da Evolution API ---


def send_whatsapp_message(phone_number, message):
    """Envia uma mensagem de texto via Evolution API."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    payload = {"number": phone_number, "options": {"delay": 1200,
                                                   "presence": "composing"}, "textMessage": {"text": message}}
    try:
        response = requests.post(url, headers=headers,
                                 data=json.dumps(payload), timeout=10)
        response.raise_for_status()
        print(f"Mensagem enviada para {phone_number}: {response.status_code}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erro ao enviar mensagem para {phone_number}: {e}")
        return None

# --- Lógica do Chatbot e Webhook ---


@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint que recebe as notificações de novas mensagens da Evolution API."""
    data = request.json
    print("--- NOVO WEBHOOK RECEBIDO ---")
    print(json.dumps(data, indent=2))

    if data.get("event") == "messages.upsert" and not data["data"].get("key", {}).get("fromMe"):
        message_data = data["data"]
        msg_content = (message_data.get("message", {}).get(
            "conversation") or message_data.get("body", "")).lower().strip()
        sender_phone = message_data.get("key", {}).get("remoteJid")

        if msg_content and sender_phone:
            handle_conversation(sender_phone, msg_content)
    return jsonify({"status": "ok"}), 200


def handle_conversation(phone, message):
    """Gerencia o fluxo da conversa para o agendamento."""
    user_state = conversation_state.get(phone, {"stage": "initial"})
    # Atualiza o timestamp da interação
    user_state['last_interaction'] = time.time()

    # Funções auxiliares para gerar listas numeradas
    def list_to_text(options, intro_text):
        return intro_text + "\n\n" + "\n".join(f"*{i+1}*. {opt}" for i, opt in enumerate(options))

    def get_choice_from_message(message, options):
        try:
            choice_index = int(message) - 1
            if 0 <= choice_index < len(options):
                return options[choice_index]
        except ValueError:
            pass
        return None

    if user_state["stage"] == "initial":
        user_state["stage"] = "awaiting_menu_choice"
        msg = "⚖️ Seja bem-vindo(a), sou o assistente virtual da Dra Camilla Tannure. Para facilitar o contato e melhorar sua experiência, como posso te ajudar?\n\n*Digite 1 para Agendar uma Consulta*"
        send_whatsapp_message(phone, msg)

    elif user_state["stage"] == "awaiting_menu_choice":
        if message == '1':
            user_state["stage"] = "get_appointment_type"
            user_state["options"] = ["Presencial", "Remoto"]
            send_whatsapp_message(phone, list_to_text(
                user_state["options"], "Entendido. O agendamento será:"))
        else:
            send_whatsapp_message(
                phone, "Opção inválida. Por favor, digite *1* para Agendar uma Consulta.")

    elif user_state["stage"] == "get_appointment_type":
        choice = get_choice_from_message(message, user_state["options"])
        if choice:
            user_state["type"] = choice.lower()
            all_slots = get_available_slots()
            if not all_slots:
                send_whatsapp_message(
                    phone, "Desculpe, não há horários disponíveis nos próximos 30 dias.")
                conversation_state.pop(phone, None)
                return

            user_state["available_slots"] = all_slots
            days_to_show = list(all_slots.keys())[:5]
            user_state["options"] = days_to_show
            msg = list_to_text(
                days_to_show, "Perfeito. Temos disponibilidade nos seguintes dias. Por favor, digite o número correspondente:")
            user_state["stage"] = "get_date"
            send_whatsapp_message(phone, msg)
        else:
            send_whatsapp_message(
                phone, "Opção inválida. Por favor, digite *1* para Presencial ou *2* para Remoto.")

    elif user_state["stage"] == "get_date":
        choice = get_choice_from_message(message, user_state["options"])
        if choice:
            user_state["date"] = choice
            times = user_state["available_slots"][choice]
            user_state["options"] = times
            msg = list_to_text(
                times, f"Ótimo! Para o dia {choice}, temos os seguintes horários. Por favor, digite o número correspondente:")
            user_state["stage"] = "get_time"
            send_whatsapp_message(phone, msg)
        else:
            send_whatsapp_message(
                phone, "Opção inválida. Por favor, digite o número de uma das datas da lista.")

    elif user_state["stage"] == "get_time":
        choice = get_choice_from_message(message, user_state["options"])
        if choice:
            user_state["time"] = choice
            user_state["stage"] = "get_subject"
            send_whatsapp_message(
                phone, "Horário selecionado. Agora, por favor, informe de forma breve o assunto a ser tratado.")
        else:
            send_whatsapp_message(
                phone, "Opção inválida. Por favor, digite o número de um dos horários da lista.")

    elif user_state["stage"] == "get_subject":
        user_state["subject"] = message

        location_text = ""
        if user_state['type'] == 'presencial':
            location_text = f"\n▫️ *Local:* {ADDRESS}"

        msg = ("Ok, vamos confirmar seu agendamento:\n\n"
               f"▫️ *Tipo:* {user_state['type'].capitalize()}"
               f"{location_text}"
               f"\n▫️ *Assunto:* {user_state['subject']}\n"
               f"▫️ *Data:* {user_state['date']}\n"
               f"▫️ *Horário:* {user_state['time']}\n\n"
               "Posso confirmar o agendamento? Digite *Sim* ou *Não*.")
        user_state["stage"] = "confirm_booking"
        send_whatsapp_message(phone, msg)

    elif user_state["stage"] == "confirm_booking":
        if message == 'sim':
            try:
                local_tz = pytz.timezone(TIMEZONE)
                date_obj = dt.datetime.strptime(
                    user_state['date'], "%d/%m/%Y").date()
                time_obj = dt.datetime.strptime(
                    user_state['time'], "%H:%M").time()
                start_dt = local_tz.localize(
                    dt.datetime.combine(date_obj, time_obj))
                end_dt = start_dt + dt.timedelta(hours=1)

                summary = f"{user_state['subject']} - {user_state['type'].capitalize()}"
                event = create_calendar_event(
                    summary, start_dt, end_dt, phone, user_state['type'])

                if event:
                    msg = f"✅ Agendamento confirmado com sucesso!\n\n▫️ *Assunto:* {user_state['subject']}\n▫️ *Data:* {user_state['date']}\n▫️ *Horário:* {user_state['time']}"
                    meet_link = event.get('hangoutLink')
                    if user_state['type'] == 'remoto' and meet_link:
                        msg += f"\n\n🔗 *Link da Reunião:* {meet_link}"
                    elif user_state['type'] == 'presencial':
                        msg += f"\n\n� *Local:* {ADDRESS}"
                    send_whatsapp_message(phone, msg)
                    user_state["stage"] = "awaiting_more_help"
                    user_state["options"] = ["Sim", "Não"]
                    send_whatsapp_message(phone, list_to_text(
                        user_state["options"], "\nPosso lhe ajudar com algo mais?"))
                else:
                    send_whatsapp_message(
                        phone, "Desculpe, ocorreu um erro e não foi possível criar o agendamento.")
                    conversation_state.pop(phone, None)
            except (ValueError, KeyError) as e:
                print(f"Erro ao criar evento: {e}")
                send_whatsapp_message(
                    phone, "Ocorreu um erro interno. Por favor, reinicie a conversa.")
                conversation_state.pop(phone, None)
        elif message == 'não':
            send_whatsapp_message(
                phone, "Ok, agendamento cancelado. Se precisar de algo mais, é só chamar!")
            conversation_state.pop(phone, None)
        else:
            send_whatsapp_message(
                phone, "Por favor, responda com 'Sim' ou 'Não'.")

    elif user_state["stage"] == "awaiting_more_help":
        if message in ['1', 'sim']:
            user_state.clear()
            user_state["stage"] = "initial"
            handle_conversation(phone, "")
        else:
            send_whatsapp_message(
                phone, "Obrigada! Se precisar de algo, estarei à disposição.")
            conversation_state.pop(phone, None)

    elif user_state["stage"] == "awaiting_timeout_response":
        if message in ['sim', '1']:
            user_state["stage"] = user_state.pop('previous_stage')
            send_whatsapp_message(
                phone, "Perfeito, vamos continuar de onde paramos.")
        else:
            send_whatsapp_message(phone, "Entendido. Tenha um ótimo dia!")
            conversation_state.pop(phone, None)

    # Armazena o estado atualizado
    conversation_state[phone] = user_state

# --- Lógica de Timeout ---


def check_inactivity():
    """Função que roda em background para verificar inatividade."""
    while True:
        try:
            now = time.time()
            # Itera sobre uma cópia para evitar problemas de concorrência
            for phone, state in list(conversation_state.items()):
                last_interaction = state.get('last_interaction', now)
                stage = state.get('stage')

                # Se está aguardando resposta do timeout por mais de 30s, encerra
                if stage == 'awaiting_timeout_response' and (now - last_interaction) > FINAL_TIMEOUT_SECONDS:
                    send_whatsapp_message(
                        phone, "Sessão encerrada por inatividade. Se precisar, inicie uma nova conversa.")
                    conversation_state.pop(phone, None)
                # Se está em qualquer outro estágio por mais de 90s, envia o aviso
                elif stage != 'awaiting_timeout_response' and (now - last_interaction) > INACTIVITY_TIMEOUT_SECONDS:
                    state['previous_stage'] = stage
                    state['stage'] = 'awaiting_timeout_response'
                    state['last_interaction'] = now
                    conversation_state[phone] = state
                    msg = "Olá! Notei que não interagimos há um tempo. Você ainda precisa de ajuda? (*Sim* ou *Não*)"
                    send_whatsapp_message(phone, msg)
            time.sleep(10)  # Verifica a cada 10 segundos
        except Exception as e:
            print(f"Erro no thread de inatividade: {e}")


# --- Execução Principal ---
if __name__ == '__main__':
    print("Verificando credenciais do Google Calendar...")
    get_calendar_service()

    # Inicia o thread de verificação de inatividade
    inactivity_thread = threading.Thread(target=check_inactivity, daemon=True)
    inactivity_thread.start()

    print("Credenciais do Google OK. Iniciando o servidor Flask e o monitor de inatividade.")
    # use_reloader=False é importante para o thread
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
