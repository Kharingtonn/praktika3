from datetime import datetime, date, timedelta
from functools import wraps
import os

from apscheduler.schedulers.background import BackgroundScheduler
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
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "store.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Для продолжения необходимо войти в систему."
login_manager.login_message_category = "warning"

scheduler = BackgroundScheduler(daemon=True)


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    orders = db.relationship("Order", backref="user", lazy="dynamic", cascade="all, delete-orphan")
    notifications = db.relationship(
        "Notification", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Book(db.Model):
    __tablename__ = "book"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    author = db.Column(db.String(150), nullable=False, index=True)
    category = db.Column(db.String(100), nullable=False, index=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)

    price_buy = db.Column(db.Float, nullable=False, default=0.0)
    price_rent_2w = db.Column(db.Float, nullable=False, default=0.0)
    price_rent_1m = db.Column(db.Float, nullable=False, default=0.0)
    price_rent_3m = db.Column(db.Float, nullable=False, default=0.0)

    status = db.Column(
        db.String(30),
        nullable=False,
        default="available",
    )
    available_copies = db.Column(db.Integer, nullable=False, default=1)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    orders = db.relationship("Order", backref="book", lazy="dynamic")

    def can_be_ordered(self) -> bool:
        return self.status in ("available", "limited") and self.available_copies > 0

    def rent_price_by_days(self, days: int) -> float:
        if days == 14:
            return self.price_rent_2w
        if days == 30:
            return self.price_rent_1m
        if days == 90:
            return self.price_rent_3m
        return 0.0


class Order(db.Model):
    __tablename__ = "order"

    id = db.Column(db.Integer, primary_key=True)
    order_type = db.Column(db.String(20), nullable=False)  # buy / rent
    rent_plan = db.Column(db.String(50), nullable=True)    # 2 недели / 1 месяц / 3 месяца
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date, nullable=True)
    total_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(30), nullable=False, default="completed")  # completed / active / returned

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Notification(db.Model):
    __tablename__ = "notification"

    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False)

    reminder_key = db.Column(db.String(100), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view_func(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_unread_notifications():
    unread_count = 0
    if current_user.is_authenticated:
        unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return {"unread_notifications": unread_count}


def get_categories():
    rows = db.session.query(Book.category).filter(Book.status != "archived").distinct().order_by(Book.category.asc()).all()
    return [row[0] for row in rows]


def get_authors():
    rows = db.session.query(Book.author).filter(Book.status != "archived").distinct().order_by(Book.author.asc()).all()
    return [row[0] for row in rows]


def generate_rental_reminders():
    with app.app_context():
        today = date.today()

        active_rentals = Order.query.filter_by(order_type="rent", status="active").all()

        for order in active_rentals:
            if not order.end_date:
                continue

            days_left = (order.end_date - today).days

            if days_left == 3:
                text = (
                    f"Напоминание: срок аренды книги "
                    f'"{order.book.title}" заканчивается через 3 дня ({order.end_date.strftime("%d.%m.%Y")}).'
                )
            elif days_left == 1:
                text = (
                    f"Напоминание: срок аренды книги "
                    f'"{order.book.title}" заканчивается завтра ({order.end_date.strftime("%d.%m.%Y")}).'
                )
            elif days_left == 0:
                text = (
                    f"Напоминание: срок аренды книги "
                    f'"{order.book.title}" заканчивается сегодня ({order.end_date.strftime("%d.%m.%Y")}).'
                )
            elif days_left < 0:
                text = (
                    f"Просрочка аренды: книга "
                    f'"{order.book.title}" должна была быть возвращена '
                    f'{order.end_date.strftime("%d.%m.%Y")}.'
                )
            else:
                continue

            reminder_key = f"order:{order.id}:days:{days_left}"

            exists = Notification.query.filter_by(reminder_key=reminder_key).first()
            if exists is None:
                db.session.add(
                    Notification(
                        user_id=order.user_id,
                        message=text,
                        reminder_key=reminder_key,
                    )
                )

        db.session.commit()


def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(
            func=generate_rental_reminders,
            trigger="interval",
            hours=12,
            id="rent_reminders_job",
            replace_existing=True,
        )
        scheduler.start()


def seed_data():
    if User.query.first():
        return

    admin = User(username="admin", email="admin@example.com", is_admin=True)
    admin.set_password("admin123")

    user = User(username="reader", email="reader@example.com", is_admin=False)
    user.set_password("reader123")

    books = [
        Book(
            title="1984",
            author="George Orwell",
            category="Антиутопия",
            year=1949,
            description="Классический роман о тотальном контроле, наблюдении и борьбе личности с системой.",
            price_buy=950,
            price_rent_2w=120,
            price_rent_1m=200,
            price_rent_3m=420,
            status="available",
            available_copies=4,
        ),
        Book(
            title="Мастер и Маргарита",
            author="Михаил Булгаков",
            category="Классика",
            year=1967,
            description="Один из самых известных русских романов с философским, мистическим и сатирическим содержанием.",
            price_buy=1100,
            price_rent_2w=140,
            price_rent_1m=230,
            price_rent_3m=460,
            status="available",
            available_copies=3,
        ),
        Book(
            title="Dune",
            author="Frank Herbert",
            category="Фантастика",
            year=1965,
            description="Эпическая история о планете Арракис, власти, экологии и судьбе Пола Атрейдеса.",
            price_buy=1450,
            price_rent_2w=160,
            price_rent_1m=260,
            price_rent_3m=520,
            status="limited",
            available_copies=2,
        ),
        Book(
            title="Flowers for Algernon",
            author="Daniel Keyes",
            category="Научная фантастика",
            year=1966,
            description="Трогательный роман о человеке, интеллект которого искусственно усилили экспериментом.",
            price_buy=990,
            price_rent_2w=130,
            price_rent_1m=220,
            price_rent_3m=430,
            status="available",
            available_copies=5,
        ),
        Book(
            title="The Hobbit",
            author="J. R. R. Tolkien",
            category="Фэнтези",
            year=1937,
            description="Приключение Бильбо Бэггинса, положившее начало большому миру Средиземья.",
            price_buy=1250,
            price_rent_2w=150,
            price_rent_1m=240,
            price_rent_3m=480,
            status="available",
            available_copies=4,
        ),
        Book(
            title="Fahrenheit 451",
            author="Ray Bradbury",
            category="Антиутопия",
            year=1953,
            description="Роман о мире, где книги запрещены, а их хранение считается преступлением.",
            price_buy=980,
            price_rent_2w=120,
            price_rent_1m=210,
            price_rent_3m=410,
            status="available",
            available_copies=3,
        ),
        Book(
            title="Norwegian Wood",
            author="Haruki Murakami",
            category="Современная проза",
            year=1987,
            description="История любви, потери и взросления на фоне японской студенческой жизни.",
            price_buy=1150,
            price_rent_2w=135,
            price_rent_1m=225,
            price_rent_3m=445,
            status="limited",
            available_copies=2,
        ),
        Book(
            title="The Little Prince",
            author="Antoine de Saint-Exupery",
            category="Сказка",
            year=1943,
            description="Философская сказка о дружбе, любви, ответственности и способности видеть главное сердцем.",
            price_buy=850,
            price_rent_2w=110,
            price_rent_1m=180,
            price_rent_3m=360,
            status="available",
            available_copies=6,
        ),
        Book(
            title="Пикник на обочине",
            author="Аркадий Стругацкий, Борис Стругацкий",
            category="Фантастика",
            year=1972,
            description="Одна из самых известных советских фантастических книг о Зоне и сталкерах.",
            price_buy=1050,
            price_rent_2w=130,
            price_rent_1m=220,
            price_rent_3m=430,
            status="available",
            available_copies=3,
        ),
        Book(
            title="Преступление и наказание",
            author="Федор Достоевский",
            category="Классика",
            year=1866,
            description="Роман о вине, совести, нравственном выборе и внутреннем переломе человека.",
            price_buy=1180,
            price_rent_2w=145,
            price_rent_1m=235,
            price_rent_3m=470,
            status="available",
            available_copies=4,
        ),
    ]

    db.session.add(admin)
    db.session.add(user)
    db.session.add_all(books)
    db.session.commit()


@app.route("/")
def index():
    category = request.args.get("category", "").strip()
    author = request.args.get("author", "").strip()
    sort = request.args.get("sort", "title")

    query = Book.query.filter(Book.status != "archived")

    if category:
        query = query.filter(Book.category == category)
    if author:
        query = query.filter(Book.author == author)

    sort_map = {
        "title": Book.title.asc(),
        "author": Book.author.asc(),
        "category": Book.category.asc(),
        "year_asc": Book.year.asc(),
        "year_desc": Book.year.desc(),
    }

    query = query.order_by(sort_map.get(sort, Book.title.asc()))
    books = query.all()

    return render_template(
        "index.html",
        books=books,
        categories=get_categories(),
        authors=get_authors(),
        current_category=category,
        current_author=author,
        current_sort=sort,
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

        user = User(username=username, email=email, is_admin=False)
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


@app.route("/book/<int:book_id>")
def book_detail(book_id):
    book = Book.query.get_or_404(book_id)

    if book.status == "archived":
        abort(404)

    return render_template("book_detail.html", book=book)


@app.route("/buy/<int:book_id>", methods=["POST"])
@login_required
def buy_book(book_id):
    book = Book.query.get_or_404(book_id)

    if not book.can_be_ordered():
        flash("Книга сейчас недоступна для покупки.", "danger")
        return redirect(url_for("book_detail", book_id=book.id))

    order = Order(
        order_type="buy",
        rent_plan=None,
        start_date=date.today(),
        end_date=None,
        total_price=book.price_buy,
        status="completed",
        user_id=current_user.id,
        book_id=book.id,
    )

    book.available_copies -= 1
    if book.available_copies <= 0:
        book.available_copies = 0
        book.status = "unavailable"
    elif book.available_copies == 1:
        book.status = "limited"

    db.session.add(order)
    db.session.commit()

    flash(f'Книга "{book.title}" успешно куплена.', "success")
    return redirect(url_for("my_orders"))


@app.route("/rent/<int:book_id>", methods=["POST"])
@login_required
def rent_book(book_id):
    book = Book.query.get_or_404(book_id)

    if not book.can_be_ordered():
        flash("Книга сейчас недоступна для аренды.", "danger")
        return redirect(url_for("book_detail", book_id=book.id))

    duration_days = int(request.form.get("duration", "14"))
    plan_map = {
        14: "2 недели",
        30: "1 месяц",
        90: "3 месяца",
    }

    if duration_days not in plan_map:
        flash("Выбран неверный срок аренды.", "danger")
        return redirect(url_for("book_detail", book_id=book.id))

    total_price = book.rent_price_by_days(duration_days)
    if total_price <= 0:
        flash("Для выбранного срока аренды цена не задана.", "danger")
        return redirect(url_for("book_detail", book_id=book.id))

    today = date.today()
    end_date = today + timedelta(days=duration_days)

    order = Order(
        order_type="rent",
        rent_plan=plan_map[duration_days],
        start_date=today,
        end_date=end_date,
        total_price=total_price,
        status="active",
        user_id=current_user.id,
        book_id=book.id,
    )

    book.available_copies -= 1
    if book.available_copies <= 0:
        book.available_copies = 0
        book.status = "unavailable"
    elif book.available_copies == 1:
        book.status = "limited"

    db.session.add(order)
    db.session.commit()

    generate_rental_reminders()

    flash(
        f'Книга "{book.title}" успешно арендована до {end_date.strftime("%d.%m.%Y")}.',
        "success",
    )
    return redirect(url_for("my_orders"))


@app.route("/my-orders")
@login_required
def my_orders():
    orders = (
        Order.query.filter_by(user_id=current_user.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    return render_template("my_orders.html", orders=orders, today=date.today())


@app.route("/notifications")
@login_required
def notifications():
    user_notifications = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )
    return render_template("notifications.html", notifications=user_notifications)


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def read_all_notifications():
    notifications_to_update = Notification.query.filter_by(
        user_id=current_user.id,
        is_read=False,
    ).all()

    for item in notifications_to_update:
        item.is_read = True

    db.session.commit()
    flash("Все уведомления отмечены как прочитанные.", "success")
    return redirect(url_for("notifications"))


@app.route("/admin/books")
@admin_required
def admin_books():
    books = Book.query.order_by(Book.created_at.desc()).all()
    return render_template("admin_books.html", books=books)


@app.route("/admin/book/new", methods=["GET", "POST"])
@admin_required
def admin_new_book():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        category = request.form.get("category", "").strip()
        year_raw = request.form.get("year", "").strip()
        description = request.form.get("description", "").strip()
        price_buy_raw = request.form.get("price_buy", "0").strip()
        price_rent_2w_raw = request.form.get("price_rent_2w", "0").strip()
        price_rent_1m_raw = request.form.get("price_rent_1m", "0").strip()
        price_rent_3m_raw = request.form.get("price_rent_3m", "0").strip()
        status = request.form.get("status", "available").strip()
        available_copies_raw = request.form.get("available_copies", "1").strip()

        try:
            year = int(year_raw)
            price_buy = float(price_buy_raw)
            price_rent_2w = float(price_rent_2w_raw)
            price_rent_1m = float(price_rent_1m_raw)
            price_rent_3m = float(price_rent_3m_raw)
            available_copies = int(available_copies_raw)
        except ValueError:
            flash("Проверьте числовые поля: год, цены и количество экземпляров.", "danger")
            return render_template(
                "book_form.html",
                page_title="Добавление книги",
                submit_text="Сохранить",
                book=None,
            )

        if not title or not author or not category or not description:
            flash("Заполните все обязательные поля.", "danger")
            return render_template(
                "book_form.html",
                page_title="Добавление книги",
                submit_text="Сохранить",
                book=None,
            )

        book = Book(
            title=title,
            author=author,
            category=category,
            year=year,
            description=description,
            price_buy=price_buy,
            price_rent_2w=price_rent_2w,
            price_rent_1m=price_rent_1m,
            price_rent_3m=price_rent_3m,
            status=status,
            available_copies=max(0, available_copies),
        )

        db.session.add(book)
        db.session.commit()
        flash("Книга успешно добавлена.", "success")
        return redirect(url_for("admin_books"))

    return render_template(
        "book_form.html",
        page_title="Добавление книги",
        submit_text="Сохранить",
        book=None,
    )


@app.route("/admin/book/<int:book_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_book(book_id):
    book = Book.query.get_or_404(book_id)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        category = request.form.get("category", "").strip()
        year_raw = request.form.get("year", "").strip()
        description = request.form.get("description", "").strip()
        price_buy_raw = request.form.get("price_buy", "0").strip()
        price_rent_2w_raw = request.form.get("price_rent_2w", "0").strip()
        price_rent_1m_raw = request.form.get("price_rent_1m", "0").strip()
        price_rent_3m_raw = request.form.get("price_rent_3m", "0").strip()
        status = request.form.get("status", "available").strip()
        available_copies_raw = request.form.get("available_copies", "1").strip()

        try:
            book.year = int(year_raw)
            book.price_buy = float(price_buy_raw)
            book.price_rent_2w = float(price_rent_2w_raw)
            book.price_rent_1m = float(price_rent_1m_raw)
            book.price_rent_3m = float(price_rent_3m_raw)
            book.available_copies = max(0, int(available_copies_raw))
        except ValueError:
            flash("Проверьте числовые поля: год, цены и количество экземпляров.", "danger")
            return render_template(
                "book_form.html",
                page_title="Редактирование книги",
                submit_text="Обновить",
                book=book,
            )

        if not title or not author or not category or not description:
            flash("Заполните все обязательные поля.", "danger")
            return render_template(
                "book_form.html",
                page_title="Редактирование книги",
                submit_text="Обновить",
                book=book,
            )

        book.title = title
        book.author = author
        book.category = category
        book.description = description
        book.status = status

        if book.available_copies == 0 and book.status in ("available", "limited"):
            book.status = "unavailable"
        elif book.available_copies > 1 and book.status == "limited":
            book.status = "available"

        db.session.commit()
        flash("Книга успешно обновлена.", "success")
        return redirect(url_for("admin_books"))

    return render_template(
        "book_form.html",
        page_title="Редактирование книги",
        submit_text="Обновить",
        book=book,
    )


@app.route("/admin/book/<int:book_id>/delete", methods=["POST"])
@admin_required
def admin_delete_book(book_id):
    book = Book.query.get_or_404(book_id)

    if book.orders.count() > 0:
        flash(
            "Нельзя удалить книгу, по которой уже есть заказы. Измените статус на archived.",
            "danger",
        )
        return redirect(url_for("admin_books"))

    db.session.delete(book)
    db.session.commit()
    flash("Книга удалена из каталога.", "info")
    return redirect(url_for("admin_books"))


@app.route("/admin/orders")
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("admin_orders.html", orders=orders, today=date.today())


@app.route("/admin/order/<int:order_id>/return", methods=["POST"])
@admin_required
def admin_return_order(order_id):
    order = Order.query.get_or_404(order_id)

    if order.order_type != "rent" or order.status != "active":
        flash("Можно вернуть только активную аренду.", "danger")
        return redirect(url_for("admin_orders"))

    order.status = "returned"
    order.book.available_copies += 1

    if order.book.available_copies == 1:
        order.book.status = "limited"
    elif order.book.available_copies > 1 and order.book.status in ("limited", "unavailable"):
        order.book.status = "available"

    db.session.commit()

    flash(
        f'Аренда книги "{order.book.title}" закрыта. Экземпляр возвращен в наличие.',
        "success",
    )
    return redirect(url_for("admin_orders"))


with app.app_context():
    db.create_all()
    seed_data()
    generate_rental_reminders()

start_scheduler()

if __name__ == "__main__":
    app.run(debug=True)