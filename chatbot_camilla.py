# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify
import logging
import requests
import datetime
import os
import json
import threading
import time
import copy

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- CONFIGURAÇÃO ---
CHATBOT_VERSION = "3.5 (Race Condition Fix)"
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# --- CONFIGURAÇÃO DO GOOGLE AGENDA ---
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'primary'

# --- GESTÃO DE ESTADO BASEADA EM FICHEIROS ---
STATE_DIR = "user_states_data"
if not os.path.exists(STATE_DIR):
    os.makedirs(STATE_DIR)

# Lock para garantir que o acesso ao estado de todos os utilizadores seja atómico
user_states_lock = threading.Lock()

def get_user_state(user_id):
    """Carrega o estado do utilizador a partir de um ficheiro JSON."""
    filepath = os.path.join(STATE_DIR, f"{user_id}.json")
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"Erro ao carregar estado para {user_id}: {e}")
            return {'stage': 'start'}
    return {'stage': 'start'}

def save_user_state(user_id, state):
    """Guarda o estado do utilizador num ficheiro JSON."""
    filepath = os.path.join(STATE_DIR, f"{user_id}.json")
    try:
        with open(filepath, 'w') as f:
            json.dump(state, f, indent=4)
    except IOError as e:
        logging.error(f"Erro ao guardar estado para {user_id}: {e}")

def delete_user_state(user_id):
    """Apaga o ficheiro de estado de um utilizador."""
    filepath = os.path.join(STATE_DIR, f"{user_id}.json")
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError as e:
            logging.error(f"Erro ao apagar estado para {user_id}: {e}")


# --- FUNÇÕES DO GOOGLE AGENDA ---

def get_calendar_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                logging.error(f"Erro ao atualizar o token do Google: {e}")
                if os.path.exists('token.json'): os.remove('token.json')
                return get_calendar_service()
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0, open_browser=False)
            except Exception as e:
                logging.error(f"Ocorreu um erro durante o fluxo de autenticação: {e}")
                return None
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    try:
        service = build('calendar', 'v3', credentials=creds)
        logging.info("Serviço do Google Agenda conectado com sucesso.")
        return service
    except HttpError as error:
        logging.error(f'Ocorreu um erro ao conectar ao Google Agenda: {error}')
        return None

def find_user_event(service, user_id):
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID, timeMin=now_utc,
            q=user_id, maxResults=1, singleEvents=True, orderBy='startTime'
        ).execute()
        return events_result.get('items', [])
    except Exception as e:
        logging.error(f"Erro ao procurar evento para {user_id}: {e}")
        return None

def get_available_slots(service):
    available_slots = []
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    time_min = now_utc.isoformat()
    time_max = (now_utc + datetime.timedelta(days=14)).isoformat()
    try:
        events_result = service.events().list(
            calendarId=CALENDAR_ID, timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy='startTime'
        ).execute()
    except Exception as e:
        logging.error(f"Erro ao buscar eventos na agenda: {e}")
        return []
    busy_slots = events_result.get('items', [])
    working_hours = [datetime.time(9, 0), datetime.time(10, 30), datetime.time(15, 0), datetime.time(16, 30)]
    for day_offset in range(1, 15):
        day = (now_utc + datetime.timedelta(days=day_offset)).date()
        for slot_time in working_hours:
            slot_datetime_utc = datetime.datetime.combine(day, slot_time).replace(tzinfo=datetime.timezone.utc)
            if slot_datetime_utc < now_utc: continue
            is_busy = False
            for event in busy_slots:
                start_utc = datetime.datetime.fromisoformat(event['start'].get('dateTime'))
                end_utc = datetime.datetime.fromisoformat(event['end'].get('dateTime'))
                if start_utc <= slot_datetime_utc < end_utc:
                    is_busy = True
                    break
            if not is_busy: available_slots.append(slot_datetime_utc)
            if len(available_slots) >= 5: return available_slots
    return available_slots

def create_calendar_event(service, summary, start_time, end_time, description):
    event = {
        'summary': summary, 'description': description,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'America/Sao_Paulo'},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': 'America/Sao_Paulo'},
    }
    try:
        created_event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        logging.info(f"Evento criado: {created_event.get('htmlLink')}")
        return True
    except HttpError as error:
        logging.error(f"Não foi possível criar o evento: {error}")
        return False

def delete_calendar_event(service, event_id):
    try:
        service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        logging.info(f"Evento {event_id} apagado com sucesso.")
        return True
    except HttpError as error:
        logging.error(f"Não foi possível apagar o evento {event_id}: {error}")
        return False

# --- LÓGICA DE INATIVIDADE ---
def handle_inactivity(user_id, dados_originais, timer_id):
    logging.info(f"[Timer] Iniciando temporizador de 90s para {user_id} (ID: {timer_id})")
    time.sleep(90)

    with user_states_lock:
        state = get_user_state(user_id)
        if not state or state.get('timer_id') != timer_id:
            logging.info(f"[Timer] Timer {timer_id} para {user_id} cancelado.")
            return

        state['stage'] = 'awaiting_inactivity_response'
        save_user_state(user_id, state)
    
    inactivity_prompt = "Olá! Notei que não interagimos há um tempo. Você ainda precisa de ajuda?\n\n1. Sim\n2. Não"
    enviar_resposta_api(dados_originais, inactivity_prompt)

    logging.info(f"[Timer] Iniciando temporizador final de 30s para {user_id}")
    time.sleep(30)

    with user_states_lock:
        state = get_user_state(user_id)
        if not state or state.get('stage') != 'awaiting_inactivity_response' or state.get('timer_id') != timer_id:
            logging.info(f"[Timer] Timer final para {user_id} cancelado.")
            return

        logging.info(f"Encerrando sessão de {user_id} por inatividade.")
        final_message = "Sessão encerrada por inatividade. Se precisar, inicie uma nova conversa. Obrigado!"
        enviar_resposta_api(dados_originais, final_message)
        delete_user_state(user_id)

# --- LÓGICA PRINCIPAL DO CHATBOT ---
def processa_conversa(user_id, mensagem_usuario, service):
    state = get_user_state(user_id)
    stage = state.get('stage')
    resposta_bot = ""
    clear_state_after = False
    
    clean_message = mensagem_usuario.lower().strip()

    if stage == 'awaiting_inactivity_response':
        if clean_message == '1' or 'sim' in clean_message:
            resposta_bot = state.get('last_bot_message', "Como posso ajudar?")
            state['stage'] = state.get('last_stage', 'start')
        elif clean_message == '2' or 'não' in clean_message or 'nao' in clean_message:
            resposta_bot = "Entendido. Agradeço pelo seu tempo! Se precisar de algo mais, é só chamar."
            clear_state_after = True
        else:
            resposta_bot = "Opção inválida. Por favor, digite 1 para Sim ou 2 para Não."
    
    elif stage == 'start':
        resposta_bot = (
            "⚖️ Seja bem-vindo(a), sou o assistente virtual da Dra Camilla Tannure. "
            "Para facilitar o contato e melhorar sua experiência, como posso te ajudar?\n\n"
            "1. Agendar uma nova consulta\n"
            "2. Remarcar ou Cancelar uma consulta existente"
        )
        state['stage'] = 'awaiting_main_choice'

    elif stage == 'awaiting_main_choice':
        if clean_message == '1':
            resposta_bot = "Entendido. Antes de agendar, preciso de algumas informações.\n\nQual é a área do seu caso? (Ex: Família, Criminal, Trabalhista)"
            state['stage'] = 'qualify_case_area'
        elif clean_message == '2':
            event = find_user_event(service, user_id)
            if not event:
                resposta_bot = "Não encontrei nenhuma consulta futura agendada para você. Gostaria de agendar uma nova?\n\n1. Sim\n2. Não"
                state['stage'] = 'no_event_found'
            else:
                found_event = event[0]
                state['event_to_manage'] = found_event
                start_time = datetime.datetime.fromisoformat(found_event['start'].get('dateTime')).astimezone(datetime.timezone(datetime.timedelta(hours=-3)))
                resposta_bot = (
                    f"Encontrei sua consulta sobre '{found_event['summary']}' agendada para {start_time.strftime('%d/%m/%Y às %H:%M')}.\n\n"
                    "O que você gostaria de fazer?\n1. Remarcar\n2. Cancelar"
                )
                state['stage'] = 'manage_event_choice'
        else:
            resposta_bot = "Opção inválida. Por favor, digite 1 ou 2."

    elif stage == 'no_event_found':
        if clean_message == '1' or 'sim' in clean_message:
             resposta_bot = "Entendido. Antes de agendar, preciso de algumas informações.\n\nQual é a área do seu caso? (Ex: Família, Criminal, Trabalhista)"
             state['stage'] = 'qualify_case_area'
        else:
            resposta_bot = "Ok. Se precisar de algo mais, estou à disposição!"
            clear_state_after = True

    elif stage == 'manage_event_choice':
        if clean_message == '1':
            delete_calendar_event(service, state.get('event_to_manage', {}).get('id'))
            resposta_bot = "Sua consulta anterior foi cancelada. Vamos encontrar um novo horário. Qual é a área do seu caso?"
            state['stage'] = 'qualify_case_area'
        elif clean_message == '2':
            resposta_bot = "Tem certeza que deseja cancelar sua consulta?\n\n1. Sim\n2. Não"
            state['stage'] = 'confirm_cancellation'
        else:
            resposta_bot = "Opção inválida. Por favor, digite 1 para Remarcar ou 2 para Cancelar."
    
    elif stage == 'confirm_cancellation':
        if clean_message == '1' or 'sim' in clean_message:
            if delete_calendar_event(service, state.get('event_to_manage', {}).get('id')):
                resposta_bot = "Sua consulta foi cancelada com sucesso. A vaga já está disponível para outros clientes. Obrigado!"
            else:
                resposta_bot = "Ocorreu um erro ao tentar cancelar sua consulta."
            clear_state_after = True
        else:
            resposta_bot = "Cancelamento abortado. Sua consulta continua agendada."
            clear_state_after = True

    elif stage == 'qualify_case_area':
        state['case_area'] = mensagem_usuario
        resposta_bot = "Obrigado. E em qual cidade você reside?"
        state['stage'] = 'qualify_location'
    
    elif stage == 'qualify_location':
        state['location'] = mensagem_usuario
        resposta_bot = "Você já possui um advogado para esta causa?\n\n1. Sim\n2. Não"
        state['stage'] = 'qualify_has_lawyer'

    elif stage == 'qualify_has_lawyer':
        if clean_message == '1' or 'sim' in clean_message:
            state['has_lawyer'] = "Sim"
            proceed = True
        elif clean_message == '2' or 'não' in clean_message or 'nao' in clean_message:
            state['has_lawyer'] = "Não"
            proceed = True
        else:
            resposta_bot = "Opção inválida. Por favor, digite 1 para Sim ou 2 para Não."
            proceed = False
        
        if proceed:
            available_slots = get_available_slots(service)
            if not available_slots:
                resposta_bot = "Obrigado pelas informações. Infelizmente, não encontrei horários disponíveis na próxima semana. Por favor, tente mais tarde."
                clear_state_after = True
            else:
                state['available_slots'] = [slot.isoformat() for slot in available_slots]
                options = [f"{i + 1}. {datetime.datetime.fromisoformat(slot).astimezone(datetime.timezone(datetime.timedelta(hours=-3))).strftime('%d/%m/%Y às %H:%M')}" for i, slot in enumerate(available_slots)]
                resposta_bot = "Perfeito. Encontrei os seguintes horários disponíveis. Por favor, digite o número correspondente:\n\n" + "\n".join(options)
                state['stage'] = 'awaiting_slot_choice'

    elif stage == 'awaiting_slot_choice':
        try:
            choice_index = int(clean_message) - 1
            if 0 <= choice_index < len(state.get('available_slots', [])):
                selected_slot_iso = state['available_slots'][choice_index]
                selected_slot = datetime.datetime.fromisoformat(selected_slot_iso)
                state['selected_slot_start'] = selected_slot.isoformat()
                state['selected_slot_end'] = (selected_slot + datetime.timedelta(hours=1)).isoformat()
                resposta_bot = "Horário selecionado. Para finalizar, por favor, informe de forma breve o assunto a ser tratado."
                state['stage'] = 'awaiting_subject'
            else:
                resposta_bot = "Opção inválida."
        except (ValueError, IndexError):
            resposta_bot = "Por favor, digite apenas o número."

    elif stage == 'awaiting_subject':
        state['subject'] = mensagem_usuario
        start_time_local = datetime.datetime.fromisoformat(state['selected_slot_start']).astimezone(datetime.timezone(datetime.timedelta(hours=-3)))
        resposta_bot = (
            "Ok, vamos confirmar seu agendamento:\n\n"
            f"▫️ Assunto: {state.get('subject')}\n"
            f"▫️ Data: {start_time_local.strftime('%d/%m/%Y')}\n"
            f"▫️ Horário: {start_time_local.strftime('%H:%M')}\n\n"
            "Posso confirmar o agendamento?\n\n1. Sim\n2. Não"
        )
        state['stage'] = 'awaiting_confirmation'

    elif stage == 'awaiting_confirmation':
        if clean_message == '1' or 'sim' in clean_message:
            description = (f"Agendamento via Chatbot.\nCliente: {user_id}\nÁrea: {state.get('case_area')}\nLocal: {state.get('location')}\nJá possui advogado: {state.get('has_lawyer')}")
            success = create_calendar_event(service, f"Consulta: {state['subject']}", datetime.datetime.fromisoformat(state['selected_slot_start']), datetime.datetime.fromisoformat(state['selected_slot_end']), description)
            if success:
                resposta_bot = ("Agendamento confirmado com sucesso! Para agilizar, envie cópia do RG e documentos para camillatannure.adv@gmail.com.")
            else:
                resposta_bot = "Desculpe, ocorreu um erro ao agendar. A nossa equipa foi notificada."
            clear_state_after = True
        elif clean_message == '2' or 'não' in clean_message or 'nao' in clean_message:
            resposta_bot = "Ok, agendamento cancelado. Se precisar de algo mais, é só chamar!"
            clear_state_after = True
        else:
            resposta_bot = "Por favor, responda com 1 para Sim ou 2 para Não."

    if clear_state_after:
        delete_user_state(user_id)
    else:
        state['last_stage'] = stage
        state['last_bot_message'] = resposta_bot
        save_user_state(user_id, state)
        
    return resposta_bot

# --- FUNÇÃO DE ENVIO DE RESPOSTA ---
def enviar_resposta_api(dados_originais, texto_resposta):
    if not texto_resposta: return
    try:
        instance_name = dados_originais.get('instance')
        remetente_jid = dados_originais.get('data', {}).get('key', {}).get('remoteJid')
        api_key = dados_originais.get('apikey')
        evolution_server_url = "http://127.0.0.1:8081"
        if not all([instance_name, remetente_jid, api_key]): return
        url_envio = f"{evolution_server_url}/message/sendText/{instance_name}"
        headers = {"apikey": api_key, "Content-Type": "application/json"}
        numero_limpo = remetente_jid.split('@')[0]
        payload_resposta = {"number": numero_limpo, "text": texto_resposta}
        logging.info(f"A enviar resposta para {numero_limpo} com o payload: {payload_resposta}")
        response = requests.post(url_envio, json=payload_resposta, headers=headers)
        response.raise_for_status()
        logging.info(f"Resposta enviada com sucesso. Status: {response.status_code}")
        return True
    except Exception as e:
        logging.error(f"Erro ao enviar resposta via API: {e}")
        return False

# --- ROTA DO WEBHOOK ---
@app.route("/chat", methods=["POST"])
def chat():
    service = get_calendar_service()
    if not service: return jsonify({"status": "erro de autenticação com o google"})

    dados = request.json
    evento = dados.get("event")
    
    if evento == "messages.upsert":
        try:
            is_from_me = dados.get('data', {}).get('key', {}).get('fromMe', False)
            remote_jid = dados.get('data', {}).get('key', {}).get('remoteJid')
            mensagem_usuario = dados.get('data', {}).get('message', {}).get('conversation') or \
                               dados.get('data', {}).get('message', {}).get('extendedTextMessage', {}).get('text')

            if not remote_jid or not mensagem_usuario: return jsonify({"status": "payload inválido"})

            # --- CORREÇÃO APLICADA AQUI: LOCK GLOBAL ---
            # Garante que apenas uma mensagem por vez seja processada para qualquer utilizador, evitando race conditions.
            with user_states_lock:
                state = get_user_state(remote_jid)
                if state.get('timer_id'):
                    state['timer_id'] = None
                    save_user_state(remote_jid, state)

                if is_from_me:
                    if '@pare' in mensagem_usuario.lower():
                        state['paused'] = True
                        save_user_state(remote_jid, state)
                        logging.info(f"Chatbot pausado para o cliente {remote_jid}.")
                    elif '@ok' in mensagem_usuario.lower():
                        state['paused'] = False
                        save_user_state(remote_jid, state)
                        logging.info(f"Chatbot retomado para o cliente {remote_jid}.")
                    return jsonify({"status": "comando processado"})
                
                else:
                    if state.get('paused', False):
                        logging.info(f"Bot pausado, ignorando mensagem de {remote_jid}.")
                        return jsonify({"status": "bot pausado, mensagem ignorada"})

                    logging.info(f"Mensagem '{mensagem_usuario}' recebida de {remote_jid}")
                    resposta_bot = processa_conversa(remote_jid, mensagem_usuario, service)
                    
                    if enviar_resposta_api(dados, resposta_bot):
                        state = get_user_state(remote_jid)
                        if state: # Se o estado ainda existir (conversa não terminou)
                            timer_id = time.time()
                            state['timer_id'] = timer_id
                            save_user_state(remote_jid, state)
                            timer = threading.Thread(target=handle_inactivity, args=(remote_jid, copy.deepcopy(dados), timer_id))
                            timer.start()

        except Exception as e:
            logging.error(f"Ocorreu um erro ao processar a mensagem: {e}")
            return jsonify({"status": "erro interno"})

    return jsonify({"status": f"evento {evento} ignorado"})

# --- ROTAS E EXECUÇÃO ---
@app.route("/")
def index():
    logging.info(f"Chatbot version {CHATBOT_VERSION} is running.")
    return f"<h1>API do Chatbot de Agendamento (Versão {CHATBOT_VERSION}) está no ar!</h1>"

if __name__ == "__main__":
    get_calendar_service()
    app.run(host='0.0.0.0', port=5000)
