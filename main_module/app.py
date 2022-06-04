import os
import logging

import attr
import dotenv
import waitress
from flask import Flask, render_template, request
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import IntegerField
from wtforms.validators import DataRequired

dotenv.load_dotenv()

from schedule import Schedule

logger = logging.getLogger("app")

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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting server...")
    waitress.serve(app, host="0.0.0.0", port="8089")
