import os
import logging
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    flash,
    request,
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    login_required,
    logout_user,
    current_user,
)
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_limiter.errors import RateLimitExceeded

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'fallback-for-development')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_ENABLED'] = False

# URL хранилища для rate-limits – общий для всех воркеров gunicorn
# пример: redis://redis:6379/0
app.config['RATELIMIT_STORAGE_URL'] = os.environ.get(
    'RATELIMIT_STORAGE_URL',
    'redis://localhost:6379/0'
)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = ',      '

# Flask-Limiter c Redis (или другим внешним бекендом) для работы с несколькими воркерами[web:4][web:10][web:18]
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=app.config['RATELIMIT_STORAGE_URL'],
)

# ====== Модели ======

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ====== Формы ======

class LoginForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired()])
    password = PasswordField('Пароль', validators=[DataRequired()])
    submit = SubmitField('Войти')


class PostForm(FlaskForm):
    title = StringField('', validators=[DataRequired(), Length(max=100)])
    content = TextAreaField(' ', validators=[DataRequired()])
    submit = SubmitField('')


# ====== Инициализация БД и тестовых данных ======

with app.app_context():
    db.create_all()
    if User.query.count() == 0:
        admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
        admin = User(username='admin')
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        print('Создан пользователь admin, пароль взят из .env или admin123')

    if Post.query.count() == 0:
        test_post = Post(
            title="Тестовый пост",
            content="Это тестовый пост в SQLite. Все ли работает?"
        )
        db.session.add(test_post)
        db.session.commit()


# ====== Ключ для лимита только админ-логина ======

def admin_login_key():
    """
    Отдельный ключ для лимита админа:
    IP + username, но считаем только если username == 'admin'.
    Так в будущем можно сделать другие лимиты для обычных пользователей.[web:5]
    """
    username = request.form.get('username', '')
    if username == 'admin':
        return f"admin:{get_remote_address()}"
    # если не admin – возвращаем что-то нейтральное,
    # чтобы этот лимит фактически не применялся к обычным пользователям
    return f"user:{get_remote_address()}:{username}"


# ====== Маршруты ======

@app.route("/")
def home():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('index.html', posts=posts)


@app.route("/post/<int:post_id>")
def show_post(post_id):
    post = Post.query.get_or_404(post_id)
    return render_template('post.html', post=post)


# Лимит только для неудачных попыток логина администратора[web:5][web:4]
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit(
    "3 per minute",
    methods=["POST"],
    key_func=admin_login_key,
    deduct_when=lambda response: response.status_code == 401,
)
def login():
    print(f"=== LOGIN HIT === IP: {request.remote_addr}")
    print(f"Headers: X-Forwarded-For: {request.headers.get('X-Forwarded-For')}")

    if current_user.is_authenticated:
        return redirect(url_for('admin'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        # сейчас у тебя только админ – но это уже готово к разделению логики
        if user and user.check_password(form.password.data):
            login_user(user)
            return redirect(url_for('admin'))
        else:
            flash('ТЫ НЕ ПРОЙДЕШЬ!!!', 'danger')
            # 401, чтобы deduct_when посчитал неудачную попытку
            return render_template('login.html', form=form), 401

    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/admin')
@login_required
def admin():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template('admin.html', posts=posts)


@app.route('/admin/new', methods=['GET', 'POST'])
@login_required
def new_post():
    form = PostForm()
    if form.validate_on_submit():
        post = Post(title=form.title.data, content=form.content.data)
        db.session.add(post)
        db.session.commit()
        flash('Пост создан!', 'success')
        return redirect(url_for('admin'))
    return render_template('edit_post.html', form=form, title='Новый пост')


@app.route('/admin/edit/<int:post_id>', methods=['GET', 'POST'])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    form = PostForm(obj=post)
    if form.validate_on_submit():
        post.title = form.title.data
        post.content = form.content.data
        db.session.commit()
        flash('Пост обновлен!', 'success')
        return redirect(url_for('admin'))
    return render_template('edit_post.html', form=form, title='Редактирование поста')


@app.route('/admin/delete/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    db.session.delete(post)
    db.session.commit()
    flash('Пост удален', 'success')
    return redirect(url_for('admin'))


@app.errorhandler(RateLimitExceeded)
def rate_limit_handler(e):
    return render_template('429.html'), 429


if __name__ == "__main__":
    app.run(debug=True)
