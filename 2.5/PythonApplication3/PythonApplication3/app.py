from datetime import datetime, date
import os

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
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "travel_diary.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Для продолжения необходимо войти в систему."
login_manager.login_message_category = "warning"


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    trips = db.relationship(
        "Trip",
        backref="author",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Trip(db.Model):
    __tablename__ = "trip"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    country = db.Column(db.String(100), nullable=False, index=True)
    city = db.Column(db.String(100), nullable=False, index=True)

    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)

    story = db.Column(db.Text, nullable=False)

    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    image_url = db.Column(db.String(500), nullable=True)
    budget = db.Column(db.Float, nullable=True)

    heritage_sites = db.Column(db.Text, nullable=True)
    places_to_visit = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    @property
    def duration_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def seed_demo_data():
    if User.query.first():
        return

    user1 = User(username="anna", email="anna@example.com")
    user1.set_password("anna123")

    user2 = User(username="mark", email="mark@example.com")
    user2.set_password("mark123")

    db.session.add_all([user1, user2])
    db.session.commit()

    demo_trips = [
        Trip(
            title="Весенний Рим",
            country="Италия",
            city="Рим",
            start_date=date(2025, 4, 12),
            end_date=date(2025, 4, 18),
            story=(
                "Это было одно из самых атмосферных путешествий. "
                "Я гуляла по узким улицам, много времени провела у Колизея, "
                "вечером сидела на площади Навона и пробовала местную пасту."
            ),
            latitude=41.9028,
            longitude=12.4964,
            image_url="https://picsum.photos/seed/rome/1000/600",
            budget=780.0,
            heritage_sites="Колизей\nРимский форум\nПантеон",
            places_to_visit="Фонтан Треви\nПлощадь Навона\nВатикан",
            user_id=user1.id,
        ),
        Trip(
            title="Осенний Киото",
            country="Япония",
            city="Киото",
            start_date=date(2025, 11, 3),
            end_date=date(2025, 11, 10),
            story=(
                "Поездка в Киото запомнилась спокойствием и красными кленами. "
                "Больше всего понравились старые районы, храмы и прогулки ранним утром."
            ),
            latitude=35.0116,
            longitude=135.7681,
            image_url="https://picsum.photos/seed/kyoto/1000/600",
            budget=1450.0,
            heritage_sites="Храм Киёмидзу-дэра\nЗамок Нидзё\nЗолотой павильон",
            places_to_visit="Район Гион\nБамбуковая роща Арасияма\nФусими Инари",
            user_id=user2.id,
        ),
    ]

    db.session.add_all(demo_trips)
    db.session.commit()


@app.route("/")
def index():
    country = request.args.get("country", "").strip()
    author = request.args.get("author", "").strip()

    query = Trip.query.join(User)

    if country:
        query = query.filter(Trip.country.ilike(f"%{country}%"))

    if author:
        query = query.filter(User.username.ilike(f"%{author}%"))

    trips = query.order_by(Trip.created_at.desc()).all()

    return render_template(
        "index.html",
        trips=trips,
        current_country=country,
        current_author=author,
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
            flash("Имя пользователя должно содержать минимум 3 символа.", "danger")
            return render_template("register.html")

        if "@" not in email or "." not in email:
            flash("Введите корректный email.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Пароль должен содержать минимум 6 символов.", "danger")
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
        flash("Аккаунт успешно создан.", "success")
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


@app.route("/trip/new", methods=["GET", "POST"])
@login_required
def new_trip():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        country = request.form.get("country", "").strip()
        city = request.form.get("city", "").strip()
        start_date_raw = request.form.get("start_date", "").strip()
        end_date_raw = request.form.get("end_date", "").strip()
        story = request.form.get("story", "").strip()

        latitude_raw = request.form.get("latitude", "").strip()
        longitude_raw = request.form.get("longitude", "").strip()
        image_url = request.form.get("image_url", "").strip()
        budget_raw = request.form.get("budget", "").strip()
        heritage_sites = request.form.get("heritage_sites", "").strip()
        places_to_visit = request.form.get("places_to_visit", "").strip()

        if not title or not country or not city or not start_date_raw or not end_date_raw or not story:
            flash("Заполните все обязательные поля путешествия.", "danger")
            return render_template("new_trip.html")

        try:
            start_date = datetime.strptime(start_date_raw, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Проверьте формат дат.", "danger")
            return render_template("new_trip.html")

        if end_date < start_date:
            flash("Дата окончания не может быть раньше даты начала.", "danger")
            return render_template("new_trip.html")

        latitude = None
        longitude = None
        budget = None

        if latitude_raw:
            try:
                latitude = float(latitude_raw)
                if latitude < -90 or latitude > 90:
                    raise ValueError
            except ValueError:
                flash("Широта должна быть числом от -90 до 90.", "danger")
                return render_template("new_trip.html")

        if longitude_raw:
            try:
                longitude = float(longitude_raw)
                if longitude < -180 or longitude > 180:
                    raise ValueError
            except ValueError:
                flash("Долгота должна быть числом от -180 до 180.", "danger")
                return render_template("new_trip.html")

        if budget_raw:
            try:
                budget = float(budget_raw)
                if budget < 0:
                    raise ValueError
            except ValueError:
                flash("Стоимость путешествия должна быть положительным числом.", "danger")
                return render_template("new_trip.html")

        if image_url and not (
            image_url.startswith("http://")
            or image_url.startswith("https://")
        ):
            flash("Ссылка на изображение должна начинаться с http:// или https://", "danger")
            return render_template("new_trip.html")

        trip = Trip(
            title=title,
            country=country,
            city=city,
            start_date=start_date,
            end_date=end_date,
            story=story,
            latitude=latitude,
            longitude=longitude,
            image_url=image_url or None,
            budget=budget,
            heritage_sites=heritage_sites or None,
            places_to_visit=places_to_visit or None,
            author=current_user,
        )

        db.session.add(trip)
        db.session.commit()

        flash("Запись о путешествии успешно создана.", "success")
        return redirect(url_for("trip_detail", trip_id=trip.id))

    return render_template("new_trip.html")


@app.route("/trip/<int:trip_id>")
def trip_detail(trip_id):
    trip = Trip.query.get_or_404(trip_id)
    return render_template("trip_detail.html", trip=trip)


@app.route("/user/<username>")
def profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    trips = user.trips.order_by(Trip.created_at.desc()).all()
    return render_template("profile.html", user=user, trips=trips)


with app.app_context():
    db.create_all()
    seed_demo_data()

if __name__ == "__main__":
    app.run(debug=True)