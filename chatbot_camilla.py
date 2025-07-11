# -*- coding: utf-8 -*-

# --- ANÁLISE DO CÓDIGO ---
# O seu código Python está correto. O erro que enfrenta (status=3) acontece porque
# o serviço systemd está a tentar executar um ficheiro chamado "chatbot_camilla.py",
# mas o nome real do seu ficheiro no repositório é "app.py".
# A correção deve ser feita no ficheiro .service, alterando "chatbot_camilla:app" para "app:app".

from flask import Flask, request, jsonify

# Cria a aplicação web
# O Gunicorn irá procurar por esta variável "app" por defeito.
app = Flask(__name__)

def responder(mensagem_usuario):
    """
    Função principal para gerar uma resposta do chatbot.
    Esta função permanece a mesma.
    """
    # Garante que a mensagem não é nula antes de a processar
    if not mensagem_usuario:
        return "Por favor, envie uma mensagem."
        
    mensagem_usuario = mensagem_usuario.lower()

    if "olá" in mensagem_usuario or "oi" in mensagem_usuario:
        return "Olá! Eu sou a Milla, sua assistente virtual. Como posso ajudar?"
    elif "adeus" in mensagem_usuario or "tchau" in mensagem_usuario:
        return "Até logo! Se precisar de mais alguma coisa, é só chamar."
    elif "como você está" in mensagem_usuario:
        return "Estou a funcionar perfeitamente, obrigada por perguntar!"
    else:
        return "Desculpe, não entendi. Pode reformular a sua pergunta?"

# Cria uma "rota" ou "endpoint" para o chat
# Este URL irá aceitar pedidos para interagir com o chatbot
@app.route("/chat", methods=["POST"])
def chat():
    """
    Recebe a mensagem do utilizador via pedido web e retorna a resposta do bot.
    """
    # Obtém a mensagem do corpo do pedido (em formato JSON)
    mensagem_usuario = request.json.get("mensagem")
    
    # Gera a resposta usando a sua função original
    resposta_bot = responder(mensagem_usuario)
    
    # Retorna a resposta em formato JSON
    return jsonify({"resposta": resposta_bot})

# --- Bloco de Execução ---
# Este bloco só é executado quando se corre o ficheiro diretamente com "python app.py".
# Um servidor de produção como o Gunicorn não executa este bloco, ele importa o objeto "app" diretamente.
if __name__ == "__main__":
    # O host '0.0.0.0' torna o servidor acessível na rede.
    # A porta 5000 é usada para desenvolvimento local.
    # O modo de depuração (debug=True) foi removido pois nunca deve ser usado em produção.
    app.run(host='0.0.0.0', port=5000)
