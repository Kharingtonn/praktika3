from datetime import datetime
import os
import uuid

from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import check_password_hash, generate_password_hash
from typing import Optional

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "blog.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Сначала войдите в аккаунт."
login_manager.login_message_category = "warning"

post_tags = db.Table(
    "post_tags",
    db.Column("post_id", db.Integer, db.ForeignKey("post.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tag.id"), primary_key=True),
)


class Follow(db.Model):
    __tablename__ = "follow"

    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    followed_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    posts = db.relationship(
        "Post",
        backref="author",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    comments = db.relationship(
        "Comment",
        backref="author",
        lazy=True,
        cascade="all, delete-orphan",
    )
    following = db.relationship(
        "Follow",
        foreign_keys=[Follow.follower_id],
        backref="follower",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    followers = db.relationship(
        "Follow",
        foreign_keys=[Follow.followed_id],
        backref="followed",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def is_following(self, user: "User") -> bool:
        return (
            Follow.query.filter_by(follower_id=self.id, followed_id=user.id).first()
            is not None
        )

    def follow(self, user: "User") -> bool:
        if user.id == self.id:
            return False
        if self.is_following(user):
            return False

        db.session.add(Follow(follower_id=self.id, followed_id=user.id))
        return True

    def unfollow(self, user: "User") -> bool:
        relation = Follow.query.filter_by(
            follower_id=self.id, followed_id=user.id
        ).first()
        if relation is None:
            return False

        db.session.delete(relation)
        return True


class Tag(db.Model):
    __tablename__ = "tag"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<Tag {self.name}>"


class Post(db.Model):
    __tablename__ = "post"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    content = db.Column(db.Text, nullable=False)

    is_public = db.Column(db.Boolean, default=True, nullable=False)
    access_token = db.Column(db.String(64), unique=True, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    tags = db.relationship(
        "Tag",
        secondary=post_tags,
        backref=db.backref("posts", lazy="dynamic"),
        lazy=True,
    )
    comments = db.relationship(
        "Comment",
        backref="post",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Post {self.title}>"


class Comment(db.Model):
    __tablename__ = "comment"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


def parse_tags(raw_tags: str):
    tag_names = []

    for part in raw_tags.split(","):
        name = part.strip().lower().replace("#", "")
        if not name:
            continue

        if len(name) > 30:
            name = name[:30]

        if name not in tag_names:
            tag_names.append(name)

    tags = []
    for name in tag_names:
        tag = Tag.query.filter_by(name=name).first()
        if tag is None:
            tag = Tag(name=name)
            db.session.add(tag)
        tags.append(tag)

    return tags


def get_all_tags():
    return Tag.query.order_by(Tag.name.asc()).all()


def get_tags_for_user_posts(user_id: int, include_hidden: bool = False):
    query = (
        Tag.query.join(post_tags, Tag.id == post_tags.c.tag_id)
        .join(Post, Post.id == post_tags.c.post_id)
        .filter(Post.author_id == user_id)
    )

    if not include_hidden:
        query = query.filter(Post.is_public.is_(True))

    return query.distinct().order_by(Tag.name.asc()).all()


def apply_tag_filter(query, tag_name: str):
    if not tag_name:
        return query

    return (
        query.join(post_tags, Post.id == post_tags.c.post_id)
        .join(Tag, Tag.id == post_tags.c.tag_id)
        .filter(Tag.name == tag_name)
        .distinct()
    )


def can_view_post(post: Post, token: Optional[str] = None) -> bool:
    if post.is_public:
        return True

    if current_user.is_authenticated and current_user.id == post.author_id:
        return True

    return token is not None and token == post.access_token


@app.route("/")
def index():
    tag_name = request.args.get("tag", "").strip().lower()

    query = Post.query.filter(Post.is_public.is_(True))
    query = apply_tag_filter(query, tag_name)
    posts = query.order_by(Post.created_at.desc()).all()

    return render_template(
        "index.html",
        posts=posts,
        tags=get_all_tags(),
        current_tag=tag_name,
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(username) < 3:
            flash("Имя пользователя должно быть не короче 3 символов.", "danger")
            return render_template("register.html")

        allowed_username = username.replace("_", "").isalnum()
        if not allowed_username:
            flash(
                "Имя пользователя может содержать только буквы, цифры и символ _. ",
                "danger",
            )
            return render_template("register.html")

        if "@" not in email or "." not in email:
            flash("Введите корректный email.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Пароль должен быть не короче 6 символов.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Пароли не совпадают.", "danger")
            return render_template("register.html")

        if User.query.filter_by(username=username).first():
            flash("Пользователь с таким именем уже существует.", "danger")
            return render_template("register.html")

        if User.query.filter_by(email=email).first():
            flash("Пользователь с таким email уже существует.", "danger")
            return render_template("register.html")

        user = User(username=username, email=email)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Регистрация выполнена успешно.", "success")
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash("Неверный логин или пароль.", "danger")
            return render_template("login.html")

        login_user(user)
        flash("Вы успешно вошли в систему.", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("index"))


@app.route("/users")
def users():
    all_users = User.query.order_by(User.username.asc()).all()
    return render_template("users.html", users=all_users)


@app.route("/user/<username>")
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()

    is_owner = current_user.is_authenticated and current_user.id == user.id
    tag_name = request.args.get("tag", "").strip().lower()

    query = Post.query.filter(Post.author_id == user.id)

    if not is_owner:
        query = query.filter(Post.is_public.is_(True))

    query = apply_tag_filter(query, tag_name)
    posts = query.order_by(Post.created_at.desc()).all()

    public_post_count = Post.query.filter_by(author_id=user.id, is_public=True).count()
    total_post_count = Post.query.filter_by(author_id=user.id).count()

    return render_template(
        "profile.html",
        user=user,
        posts=posts,
        is_owner=is_owner,
        public_post_count=public_post_count,
        total_post_count=total_post_count,
        tags=get_tags_for_user_posts(user.id, include_hidden=is_owner),
        current_tag=tag_name,
    )


@app.route("/follow/<username>", methods=["POST"])
@login_required
def follow_user(username):
    user = User.query.filter_by(username=username).first_or_404()

    if current_user.follow(user):
        db.session.commit()
        flash(f"Вы подписались на @{user.username}.", "success")
    else:
        flash("Подписка не выполнена.", "warning")

    return redirect(request.referrer or url_for("profile", username=username))


@app.route("/unfollow/<username>", methods=["POST"])
@login_required
def unfollow_user(username):
    user = User.query.filter_by(username=username).first_or_404()

    if current_user.unfollow(user):
        db.session.commit()
        flash(f"Вы отписались от @{user.username}.", "info")
    else:
        flash("Отписка не выполнена.", "warning")

    return redirect(request.referrer or url_for("profile", username=username))


@app.route("/feed")
@login_required
def feed():
    tag_name = request.args.get("tag", "").strip().lower()

    followed_ids = db.session.query(Follow.followed_id).filter(
        Follow.follower_id == current_user.id
    )

    query = Post.query.filter(
        Post.is_public.is_(True),
        or_(Post.author_id.in_(followed_ids), Post.author_id == current_user.id),
    )

    query = apply_tag_filter(query, tag_name)
    posts = query.order_by(Post.created_at.desc()).all()

    return render_template(
        "feed.html",
        posts=posts,
        tags=get_all_tags(),
        current_tag=tag_name,
    )


@app.route("/post/new", methods=["GET", "POST"])
@login_required
def new_post():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        visibility = request.form.get("visibility", "public")
        raw_tags = request.form.get("tags", "")

        if not title:
            flash("Укажите заголовок поста.", "danger")
            return render_template(
                "post_form.html",
                page_title="Новый пост",
                submit_text="Опубликовать",
                post=None,
            )

        if not content:
            flash("Текст поста не должен быть пустым.", "danger")
            return render_template(
                "post_form.html",
                page_title="Новый пост",
                submit_text="Опубликовать",
                post=None,
            )

        post = Post(
            title=title,
            content=content,
            author=current_user,
            is_public=(visibility == "public"),
        )

        if not post.is_public:
            post.access_token = uuid.uuid4().hex

        post.tags = parse_tags(raw_tags)

        db.session.add(post)
        db.session.commit()

        flash("Пост успешно создан.", "success")

        if post.is_public:
            return redirect(url_for("post_detail", post_id=post.id))

        return redirect(url_for("hidden_post_detail", token=post.access_token))

    return render_template(
        "post_form.html",
        page_title="Новый пост",
        submit_text="Опубликовать",
        post=None,
    )


@app.route("/post/<int:post_id>")
def post_detail(post_id):
    post = Post.query.get_or_404(post_id)
    token = request.args.get("token")

    if not can_view_post(post, token):
        abort(404)

    return render_template("post_detail.html", post=post, token=token)


@app.route("/hidden/<token>")
def hidden_post_detail(token):
    post = Post.query.filter_by(access_token=token, is_public=False).first_or_404()
    return render_template("post_detail.html", post=post, token=token)


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.author_id != current_user.id:
        abort(403)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        visibility = request.form.get("visibility", "public")
        raw_tags = request.form.get("tags", "")

        if not title:
            flash("Укажите заголовок поста.", "danger")
            return render_template(
                "post_form.html",
                page_title="Редактирование поста",
                submit_text="Сохранить",
                post=post,
            )

        if not content:
            flash("Текст поста не должен быть пустым.", "danger")
            return render_template(
                "post_form.html",
                page_title="Редактирование поста",
                submit_text="Сохранить",
                post=post,
            )

        was_public = post.is_public

        post.title = title
        post.content = content
        post.is_public = visibility == "public"

        if post.is_public:
            post.access_token = None
        elif was_public or not post.access_token:
            post.access_token = uuid.uuid4().hex

        post.tags = parse_tags(raw_tags)

        db.session.commit()
        flash("Пост успешно обновлен.", "success")

        if post.is_public:
            return redirect(url_for("post_detail", post_id=post.id))

        return redirect(url_for("hidden_post_detail", token=post.access_token))

    return render_template(
        "post_form.html",
        page_title="Редактирование поста",
        submit_text="Сохранить",
        post=post,
    )


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)

    if post.author_id != current_user.id:
        abort(403)

    db.session.delete(post)
    db.session.commit()

    flash("Пост удален.", "info")
    return redirect(url_for("profile", username=current_user.username))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    post = Post.query.get_or_404(post_id)
    token = request.args.get("token")
    content = request.form.get("content", "").strip()

    if not can_view_post(post, token):
        abort(404)

    if not content:
        flash("Комментарий не должен быть пустым.", "danger")
    else:
        comment = Comment(content=content, author=current_user, post=post)
        db.session.add(comment)
        db.session.commit()
        flash("Комментарий добавлен.", "success")

    if post.is_public:
        return redirect(url_for("post_detail", post_id=post.id))

    if token == post.access_token:
        return redirect(url_for("hidden_post_detail", token=post.access_token))

    return redirect(url_for("post_detail", post_id=post.id))


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)