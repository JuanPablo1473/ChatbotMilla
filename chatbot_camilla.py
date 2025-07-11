# -*- coding: utf-8 -*-

# Código do Chatbot Milla com um servidor web Flask
from flask import Flask, request, jsonify

# Cria a aplicação web
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

# Função principal para executar o servidor web
if __name__ == "__main__":
    # O host '0.0.0.0' torna o servidor acessível na rede
    # Pode alterar a porta se necessário
    app.run(host='0.0.0.0', port=5000, debug=True)
