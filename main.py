import os
import re
import math
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
