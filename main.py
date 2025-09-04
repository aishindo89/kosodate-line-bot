# main.py
import os
import json
import google.generativeai as genai
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

try:
    with open('services.json', 'r', encoding='utf-8') as f:
        services_data = json.load(f)
except FileNotFoundError:
    services_data = []

def search_services(query):
    results = []
    for service in services_data:
        match_count = 0
        for keyword in service.get('keywords', []):
            if keyword in query:
                match_count += 1
        if service['name'] in query:
            match_count += 2
        
        if match_count > 0:
            results.append({'service': service, 'score': match_count})
    
    sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)
    return [item['service'] for item in sorted_results[:3]]

system_prompt = """
あなたは「アイちゃん」です。元保育士で、現在は静岡県富士市役所に勤務しているという設定の、親しみやすいAIアシスタントです。
あなたの主な仕事は、富士市に住む子育て中のパパやママをサポートすることです。

# 守るべきルール
- 必ず「アイちゃん」として応答してください。
- 300文字以内で応答してください。
- ユーザーからの質問や相談に対して、まずは共感の言葉を伝えてください。
- **これから提示する「関連サービス情報」を元にして、ユーザーの質問に最も適した回答を生成してください。**
- **回答には、関連するサービスのURLを必ず含めてください。**
- 提示された情報で回答が難しい場合は、「その件については、こちらの富士市の公式サイトをご確認いただけますか？」と正直に伝えてください。
- 医療的な判断が必要な相談には直接答えず、「かかりつけの小児科医や、市の保健センターにご相談くださいね」と優しく促してください。
- 回答の最後は、ユーザーを応援するポジティブな言葉で締めくくってください。

"""

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')

if not all([GEMINI_API_KEY, LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN]):
    raise ValueError("環境変数が設定されていません。")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=system_prompt)

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_message = event.message.text
    
    relevant_services = search_services(user_message)
    
    # --- ▼▼▼ここから変更点▼▼▼ ---

    # AIへの指示（プロンプト）を組み立てる
    # 1. まず、Web検索を依頼する指示を記述
    context_prompt = (
        f"ユーザーは「{user_message}」と質問しています。\n\n"
        "# タスク1: 一般的な回答の生成\n"
        "まず、この質問に対して、一般的なアドバイスや解決策をあなたの知識とウェブ検索能力を駆使して、親しみやすく回答してください。\n\n"
    )

    # 2. services.jsonから関連情報が見つかった場合、追加の指示を追記
    if relevant_services:
        context_prompt += (
            "# タスク2: 富士市の公式情報の紹介\n"
            "その上で、もし以下の富士市の公式サービス情報の中に、この質問の解決に役立ちそうなものがあれば、「富士市には、こんなサポートもありますよ」といった形で、自然な流れで紹介してください。回答には必ずサービスのURLを含めてください。\n\n"
            "# 関連サービス情報\n"
        )
        for service in relevant_services:
            context_prompt += f"- サービス名: {service['name']}\n  - 説明: {service['description']}\n  - URL: {service['url']}\n"
    
    # 3. 最後に、回答の形式を指示
    context_prompt += (
        "\n# 回答の形式\n"
        "上記タスク1とタスク2の結果を組み合わせて、一つの自然でまとまりのある文章として回答を生成してください。"
    )

    # --- ▲▲▲ここまで変更点▲▲▲ ---

    try:
        chat_session = model.start_chat(history=[])
        response = chat_session.send_message(context_prompt)
        gemini_reply = response.text
    except Exception as e:
        app.logger.error(f"Gemini API Error: {e}")
        gemini_reply = "ごめんなさい、今ちょっと考えがまとまらないみたいです。少し時間を置いてからもう一度試してみてください。"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=gemini_reply)]
            )
        )

if __name__ == "__main__":
    app.run(port=8080)