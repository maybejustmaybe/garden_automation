import os

import attr
from flask_wtf import FlaskForm
from wtforms import IntegerField
from wtforms.validators import DataRequired
from flask import Flask, request, render_template
from flask_wtf.csrf import CSRFProtect

from schedule import Schedule

import dotenv
dotenv.load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
csrf = CSRFProtect(app)


class ScheduleForm(FlaskForm):
    morning = IntegerField("morning", validators=[DataRequired()])
    afternoon = IntegerField("afternoon", validators=[DataRequired()])
    night = IntegerField("night", validators=[DataRequired()])


cur_schedule = Schedule.load()


@app.route("/", methods=["GET", "POST"])
def home():
    form = ScheduleForm(**attr.asdict(cur_schedule))

    if request.method == "POST":
        form.populate_obj(cur_schedule)
        cur_schedule.dump()

    return render_template("home.html", form=form)
