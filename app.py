from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Модель поста (таблица в базе)
class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Post {self.title}>'

# Создаём базу и добавляем тестовый пост
with app.app_context():
    db.create_all()
    # Проверяем, есть ли посты
    if Post.query.count() == 0:
        test_post = Post(
            title="Первый пост из базы данных",
            content="Этот пост хранится в SQLite. Магия, правда?"
        )
        db.session.add(test_post)
        db.session.commit()

@app.route("/")
def home():
    posts = Post.query.all()
    return render_template('index.html', posts=posts)

if __name__ == "__main__":
    app.run(debug=True)
