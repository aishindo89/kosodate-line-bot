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

# ユーザーごとの会話履歴を保存するための辞書
conversation_history = {}


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

# --- ▼▼▼ 修正点：system_promptにURLに関する厳格なルールを追加 ▼▼▼ ---
system_prompt = """
あなたは「アイちゃん」です。元保育士で、現在は静岡県富士市役所に勤務しているという設定の、親しみやすいAIアシスタントです。
あなたの主な仕事は、富士市に住む子育て中のパパやママをサポートすることです。会話を通じて、ユーザーの悩みに寄り添い、解決の手助けをしてください。

# 振る舞いの基本ルール
- 必ず「アイちゃん」として応答してください。
- 常に丁寧語を使い、共感と優しさを忘れないでください。
- **市の公式サービスについて言及する際は、必ず提供された公式URLを使用してください。不明な場合や提供されていない場合は、架空のURLや例（example.comなど）を決して生成してはいけません。これは最も重要なルールです。**
- 医療的な判断が必要な相談には直接答えず、「かかりつけの小児科医や、市の保健センターにご相談くださいね」と優しく促してください。
- 回答の最後は、ユーザーを応援するポジティブな言葉で締めくくることが多いです。
- 全体の回答は250文字以内を目安に、簡潔にまとめてください。
"""
# --- ▲▲▲ 修正点 ▲▲▲ ---


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
    user_id = event.source.user_id # ユーザーを識別するためのIDを取得
    user_message = event.message.text
    
    # ユーザーIDに基づいて会話セッションを取得、なければ新規作成
    if user_id not in conversation_history:
        conversation_history[user_id] = model.start_chat(history=[])
    chat_session = conversation_history[user_id]
    
    relevant_services = search_services(user_message)
    
    # --- ▼▼▼ 修正点：AIへの指示書の構造を全面的に改良 ▼▼▼ ---
    context_prompt = f"ユーザーは「{user_message}」と質問しています。\n\n"

    # 最初に利用可能なツール（市の公式情報）を提示する
    if relevant_services:
        context_prompt += "# あなたが利用できる富士市の公式情報リスト（信頼できる情報源）\n"
        for service in relevant_services:
            context_prompt += f"- サービス名: {service['name']}\n  - 説明: {service['description']}\n  - URL: {service['url']}\n"
        context_prompt += "\n"

    # 次に、提示したツールをどう使うかの具体的な指示を与える
    context_prompt += (
        "# あなたへの指示\n"
        "1. まず、ユーザーの質問に対し、あなたの知識とウェブ検索能力を使って一般的な回答やアドバイスを生成してください。\n"
        "2. 次に、上記の「あなたが利用できる富士市の公式情報リスト」の中に、ユーザーの質問に役立つ情報があるか検討してください。\n"
        "3. もし役立つ情報があると判断した場合のみ、あなたの一般的な回答に続けて、「富士市には、このようなサポートもありますよ」のように紹介してください。\n"
        "4. **【最重要ルール】** 公式情報を紹介する際は、リストに書かれている**サービス名、説明、URLを、一言一句変えずにそのまま使用してください。** あなたの知識で情報を補ったり、**架空のURLを創作することは絶対に禁止します。**\n"
        "5. もしユーザーの質問が曖昧で、より的確な回答をするために追加の情報が必要な場合は、回答の最後に、ユーザーが答えやすいような具体的な質問を一つか二つ付け加えてください。\n"
        "6. 上記の指示をすべて考慮し、一つの自然でまとまりのある文章として回答を作成してください。"
    )
    # --- ▲▲▲ 修正点 ▲▲▲ ---

    try:
        # ユーザーごとの会話セッションを使って対話する
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

