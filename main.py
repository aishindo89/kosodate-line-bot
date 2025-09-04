# main.py
import os
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

# 環境変数からAPIキーなどを取得
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')

if not all([GEMINI_API_KEY, LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN]):
    raise ValueError("環境変数が設定されていません。")

# Gemini APIの設定
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # 最新の軽量モデル

# LINE Bot SDKの設定
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Geminiに投げるプロンプト（ここでAIの役割を定義！）
system_prompt = """
あなたは、子育て中のパパやママをサポートする、経験豊富で優しい保育士です。
ユーザーからの質問や相談に対して、以下のルールに従って、親しみやすい言葉で回答してください。

# ルール
- 専門用語は避け、分かりやすい言葉で説明してください。
- 回答は具体的で、すぐに実践できるようなアドバイスを心がけてください。
- ユーザーの不安な気持ちに寄り添い、共感する姿勢を示してください。
- 医療的な判断が必要な相談には直接答えず、「かかりつけの小児科医や専門家にご相談ください」と促してください。
- ポジティブで、安心感を与えるようなトーンで話してください。
"""

# ユーザーごとの会話履歴を保存するシンプルな辞書
conversation_history = {}

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
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        user_id = event.source.user_id
        user_message = event.message.text

        # ユーザーIDに基づいて会話履歴を取得、なければ初期化
        if user_id not in conversation_history:
            conversation_history[user_id] = model.start_chat(history=[
                {'role': 'user', 'parts': [system_prompt]},
                {'role': 'model', 'parts': ["はい、承知いたしました。私は子育てをサポートする保育士として、ご質問にお答えしますね。どんなことでも気軽にご相談ください。"]}
            ])

        # Geminiにメッセージを送信し、応答を取得
        chat_session = conversation_history[user_id]
        try:
            response = chat_session.send_message(user_message)
            gemini_reply = response.text
        except Exception as e:
            app.logger.error(f"Gemini API Error: {e}")
            gemini_reply = "ごめんなさい、今ちょっと考えがまとまらないみたいです。少し時間を置いてからもう一度試してみてください。"

        # LINEで返信する
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=gemini_reply)]
            )
        )

if __name__ == "__main__":
    # Renderはgunicornを使うので、この部分はローカルテスト用
    app.run(port=8080)