# main.py
import os
import json # JSONファイルを扱うために追加
import google.generativeai as genai
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

app = Flask(__name__)

# --- ▼▼▼ここから変更点▼▼▼ ---

# services.json ファイルを読み込む
try:
    with open('services.json', 'r', encoding='utf-8') as f:
        services_data = json.load(f)
except FileNotFoundError:
    services_data = [] # ファイルがなくてもエラーにならないようにする

# ユーザーのメッセージに関連するサービスを検索する関数
def search_services(query):
    # 簡単なキーワード検索（複数のキーワードにマッチするものを優先）
    results = []
    for service in services_data:
        match_count = 0
        for keyword in service.get('keywords', []):
            if keyword in query:
                match_count += 1
        # サービス名が直接含まれている場合もマッチとみなす
        if service['name'] in query:
            match_count += 2 # サービス名は重要度を高くする
        
        if match_count > 0:
            results.append({'service': service, 'score': match_count})
    
    # マッチ数が多い順に並び替え
    sorted_results = sorted(results, key=lambda x: x['score'], reverse=True)
    # 上位3件までの情報を返す
    return [item['service'] for item in sorted_results[:3]]


# Geminiに投げるプロンプト（AIの役割定義）- 情報をリストアップする部分を削除
system_prompt = """
あなたは「アイちゃん」です。元保育士で、現在は静岡県富士市役所に勤務しているという設定の、親しみやすいAIアシスタントです。
あなたの主な仕事は、富士市に住む子育て中のパパやママをサポートすることです。

# 守るべきルール
- 必ず「AIアイちゃん」として応答してください。
-200文字以内で応答してください。
- ユーザーからの質問や相談に対して、まずは共感の言葉を伝えてください。
- **これから提示する「関連サービス情報」を元にして、ユーザーの質問に最も適した回答を生成してください。**
- **回答には、関連するサービスのURLを必ず含めてください。**
- 提示された情報で回答が難しい場合は、「その件については、こちらの富士市の公式サイトをご確認いただけますか？」と正直に伝えてください。
- 医療的な判断が必要な相談には直接答えず、「かかりつけの小児科医や、市の保健センターにご相談くださいね」と優しく促してください。
- 回答の最後は、ユーザーを応援するポジティブな言葉で締めくくってください。
"""

# --- ▲▲▲ここまで変更点▲▲▲ ---

# 環境変数からAPIキーなどを取得
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')

if not all([GEMINI_API_KEY, LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN]):
    raise ValueError("環境変数が設定されていません。")

# Gemini APIの設定
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash', system_instruction=system_prompt) # system_promptをここに設定

# LINE Bot SDKの設定
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
    
    # --- ▼▼▼ここから変更点▼▼▼ ---
    
    # 1. ユーザーのメッセージに関連するサービスを検索
    relevant_services = search_services(user_message)
    
    # 2. AIに渡すための追加情報を作成
    if relevant_services:
        context_prompt = "ユーザーは「{}」と質問しています。以下の関連サービス情報を元に、最適な回答を作成してください。\n\n# 関連サービス情報\n".format(user_message)
        for service in relevant_services:
            context_prompt += "- サービス名: {}\n  - 説明: {}\n  - URL: {}\n".format(service['name'], service['description'], service['url'])
    else:
        context_prompt = "ユーザーは「{}」と質問しています。関連する市役所のサービスが見つかりませんでした。その旨を伝え、富士市の公式サイト（https://www.city.fuji.shizuoka.jp/）を確認するように優しく促してください。".format(user_message)

    # --- ▲▲▲ここまで変更点▲▲▲ ---

    try:
        chat_session = model.start_chat(history=[]) # 会話履歴は毎回リセット
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