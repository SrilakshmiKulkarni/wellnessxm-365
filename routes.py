from datetime import datetime, date, timedelta
from functools import wraps
import json
import logging
import os

from flask import render_template, redirect, url_for, request, flash, jsonify, session, Response
from flask_login import login_user, logout_user, current_user, login_required
import os
from requests_oauthlib import OAuth2Session
from sqlalchemy import func, desc
from werkzeug.security import generate_password_hash
from openai import OpenAI

from app import app, db
from models import User, UserProfile, Diet, Weight, Water, Exercise, Mood, Reminder

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Helper functions
def get_total_calories_for_date(user_id, target_date):
    """Get total calories consumed for a specific date"""
    return db.session.query(func.sum(Diet.calories)).filter(
        Diet.user_id == user_id,
        Diet.date == target_date
    ).scalar() or 0

def get_water_for_date(user_id, target_date):
    """Get total water intake for a specific date"""
    return db.session.query(func.sum(Water.amount)).filter(
        Water.user_id == user_id,
        Water.date == target_date
    ).scalar() or 0

def get_calories_burned_for_date(user_id, target_date):
    """Get total calories burned for a specific date"""
    return db.session.query(func.sum(Exercise.calories_burned)).filter(
        Exercise.user_id == user_id,
        Exercise.date == target_date
    ).scalar() or 0

def get_weight_data(user_id, days=30):
    """Get weight data for the past X days"""
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    weights = Weight.query.filter(
        Weight.user_id == user_id,
        Weight.date >= start_date,
        Weight.date <= end_date
    ).order_by(Weight.date).all()
    
    dates = []
    weight_values = []
    
    for entry in weights:
        dates.append(entry.date.strftime('%Y-%m-%d'))
        weight_values.append(entry.weight)
    
    return dates, weight_values

def get_today_stats(user_id):
    today = date.today()
    stats = {
        'calories_consumed': get_total_calories_for_date(user_id, today),
        'water_intake': get_water_for_date(user_id, today),
        'calories_burned': get_calories_burned_for_date(user_id, today),
    }
    
    # Get user's daily goals
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    if profile:
        stats['calorie_goal'] = profile.calorie_goal
        stats['water_goal'] = profile.water_goal
    else:
        stats['calorie_goal'] = 2000
        stats['water_goal'] = 2000
    
    return stats

# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            
            # Set theme from user profile
            profile = UserProfile.query.filter_by(user_id=user.id).first()
            if profile and profile.theme:
                session['theme'] = profile.theme
            
            # Set flag to show loading screen after login
            session['show_loading_screen'] = True
            
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return render_template('register.html')
            
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
            return render_template('register.html')
            
        if password != confirm_password:
            flash('Passwords do not match', 'danger')
            return render_template('register.html')
        
        # Create user
        user = User(username=username, email=email)
        user.set_password(password)
        
        # Create user profile
        profile = UserProfile(
            user=user,
            theme='orange',
            calorie_goal=2000,
            water_goal=2000
        )
        
        db.session.add(user)
        db.session.add(profile)
        db.session.commit()
        
        # Set flag to show loading screen after first login
        session['first_login'] = True
        
        flash('Account created successfully. Please log in.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    today_stats = get_today_stats(current_user.id)
    
    # Get the most recent entries
    recent_meals = Diet.query.filter_by(user_id=current_user.id).order_by(Diet.created_at.desc()).limit(5).all()
    recent_exercises = Exercise.query.filter_by(user_id=current_user.id).order_by(Exercise.created_at.desc()).limit(5).all()
    recent_moods = Mood.query.filter_by(user_id=current_user.id).order_by(Mood.created_at.desc()).limit(5).all()
    
    # Get latest weight
    latest_weight = Weight.query.filter_by(user_id=current_user.id).order_by(Weight.date.desc()).first()
    
    # Get dates and weights for the chart
    dates, weights = get_weight_data(current_user.id, days=30)
    
    # Format data for our updated dashboard
    stats = {
        'calories': today_stats.get('calories_consumed', 0),
        'calories_percent': min(100, int(today_stats.get('calories_consumed', 0) / max(1, today_stats.get('calorie_goal', 2000)) * 100)),
        'water': today_stats.get('water_intake', 0),
        'water_percent': min(100, int(today_stats.get('water_intake', 0) / max(1, today_stats.get('water_goal', 2000)) * 100)),
        'exercise_calories': today_stats.get('calories_burned', 0),
        'exercise_percent': min(100, int(today_stats.get('calories_burned', 0) / 500 * 100)),  # Assuming 500 calories is a good daily goal
        'current_weight': latest_weight.weight if latest_weight else 0,
        'weight_percent': 100  # Will calculate properly if we have a goal weight
    }
    
    # Prepare updates list for the dashboard
    updates = []
    
    # Add recent meals
    for meal in recent_meals[:2]:
        updates.append({
            'icon': 'utensils',
            'title': f"{meal.food_name} ({meal.calories} cal)",
            'time': meal.created_at.strftime('%H:%M, %b %d'),
            'color': 'rgba(255, 152, 0, 0.1)'
        })
    
    # Add recent exercises
    for exercise in recent_exercises[:2]:
        updates.append({
            'icon': 'running',
            'title': f"{exercise.activity} ({exercise.duration} min)",
            'time': exercise.created_at.strftime('%H:%M, %b %d'),
            'color': 'rgba(76, 175, 80, 0.1)'
        })
    
    # Add recent moods
    for mood in recent_moods[:1]:
        updates.append({
            'icon': 'smile',
            'title': f"Mood: {mood.mood_description or 'Level ' + str(mood.mood_level)}",
            'time': mood.created_at.strftime('%H:%M, %b %d'),
            'color': 'rgba(33, 150, 243, 0.1)'
        })
    
    # Check if we need to show loading screen
    show_loading = False
    if session.get('show_loading_screen'):
        show_loading = True
        session.pop('show_loading_screen', None)
    elif session.get('first_login'):
        show_loading = True
        session.pop('first_login', None)
    
    return render_template(
        'dashboard.html',
        stats=stats,
        updates=updates,
        weight_dates=json.dumps(dates),
        weight_values=json.dumps(weights),
        show_loading=show_loading
    )

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user_profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    
    if request.method == 'POST':
        # Update profile
        if not user_profile:
            user_profile = UserProfile(user_id=current_user.id)
            db.session.add(user_profile)
        
        user_profile.name = request.form.get('name', '')
        user_profile.age = request.form.get('age', type=int)
        user_profile.gender = request.form.get('gender', '')
        user_profile.height = request.form.get('height', type=float)
        user_profile.weight_goal = request.form.get('weight_goal', type=float)
        user_profile.calorie_goal = request.form.get('calorie_goal', type=int)
        user_profile.water_goal = request.form.get('water_goal', type=int)
        user_profile.theme = request.form.get('theme', 'orange')
        user_profile.fitness_goal = request.form.get('fitness_goal', '')
        
        # Update session theme
        session['theme'] = user_profile.theme
        
        db.session.commit()
        flash('Profile updated successfully', 'success')
        return redirect(url_for('profile'))
    
    return render_template('profile.html', profile=user_profile)

@app.route('/diet', methods=['GET', 'POST'])
@login_required
def diet():
    if request.method == 'POST':
        meal_type = request.form.get('meal_type')
        food_name = request.form.get('food_name')
        calories = request.form.get('calories', type=int)
        carbs = request.form.get('carbs', type=float)
        protein = request.form.get('protein', type=float)
        fat = request.form.get('fat', type=float)
        meal_date = request.form.get('date')
        
        try:
            meal_date = datetime.strptime(meal_date, '%Y-%m-%d').date()
        except:
            meal_date = date.today()
        
        meal = Diet(
            user_id=current_user.id,
            date=meal_date,
            meal_type=meal_type,
            food_name=food_name,
            calories=calories,
            carbs=carbs,
            protein=protein,
            fat=fat
        )
        
        db.session.add(meal)
        db.session.commit()
        flash('Meal added successfully', 'success')
        return redirect(url_for('diet'))
    
    # Get meals for today by default
    today = date.today()
    selected_date = request.args.get('date', today.strftime('%Y-%m-%d'))
    
    try:
        selected_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
    except:
        selected_date = today
    
    meals = Diet.query.filter_by(
        user_id=current_user.id,
        date=selected_date
    ).order_by(Diet.meal_type, Diet.created_at).all()
    
    # Calculate totals
    total_calories = sum(meal.calories or 0 for meal in meals)
    total_carbs = sum(meal.carbs or 0 for meal in meals)
    total_protein = sum(meal.protein or 0 for meal in meals)
    total_fat = sum(meal.fat or 0 for meal in meals)
    
    # Get user's calorie goal
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    calorie_goal = profile.calorie_goal if profile else 2000
    
    # Calculate previous and next dates for navigation
    prev_date = selected_date - timedelta(days=1)
    next_date = selected_date + timedelta(days=1)
    
    return render_template(
        'diet.html',
        meals=meals,
        selected_date=selected_date,
        total_calories=total_calories,
        total_carbs=total_carbs,
        total_protein=total_protein,
        total_fat=total_fat,
        calorie_goal=calorie_goal,
        prev_date=prev_date,
        next_date=next_date,
        timedelta=timedelta
    )

@app.route('/weight', methods=['GET', 'POST'])
@login_required
def weight():
    if request.method == 'POST':
        weight_val = request.form.get('weight', type=float)
        weight_date = request.form.get('date')
        notes = request.form.get('notes', '')
        
        try:
            weight_date = datetime.strptime(weight_date, '%Y-%m-%d').date()
        except:
            weight_date = date.today()
        
        # Check if there's already a weight entry for this date
        existing = Weight.query.filter_by(
            user_id=current_user.id,
            date=weight_date
        ).first()
        
        if existing:
            existing.weight = weight_val
            existing.notes = notes
            flash('Weight updated successfully', 'success')
        else:
            weight_entry = Weight(
                user_id=current_user.id,
                date=weight_date,
                weight=weight_val,
                notes=notes
            )
            db.session.add(weight_entry)
            flash('Weight entry added successfully', 'success')
        
        db.session.commit()
        return redirect(url_for('weight'))
    
    # Get weight entries
    weights = Weight.query.filter_by(
        user_id=current_user.id
    ).order_by(Weight.date.desc()).all()
    
    # Get data for the chart
    dates, weight_values = get_weight_data(current_user.id)
    
    # Get goal weight
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    goal_weight = profile.weight_goal if profile else None
    
    return render_template(
        'weight.html',
        weights=weights,
        dates=json.dumps(dates),
        weight_values=json.dumps(weight_values),
        goal_weight=goal_weight
    )

@app.route('/water', methods=['GET', 'POST'])
@login_required
def water():
    if request.method == 'POST':
        amount = request.form.get('amount', type=int)
        water_date = request.form.get('date')
        
        try:
            water_date = datetime.strptime(water_date, '%Y-%m-%d').date()
        except:
            water_date = date.today()
        
        water_entry = Water(
            user_id=current_user.id,
            date=water_date,
            amount=amount
        )
        
        db.session.add(water_entry)
        db.session.commit()
        flash('Water intake added successfully', 'success')
        return redirect(url_for('water'))
    
    # Get water entries for today by default
    today = date.today()
    selected_date = request.args.get('date', today.strftime('%Y-%m-%d'))
    
    try:
        selected_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
    except:
        selected_date = today
    
    water_entries = Water.query.filter_by(
        user_id=current_user.id,
        date=selected_date
    ).order_by(Water.created_at).all()
    
    # Calculate total
    total_water = sum(entry.amount for entry in water_entries)
    
    # Get user's water goal
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    water_goal = profile.water_goal if profile else 2000
    
    # Get data for the last 7 days
    end_date = date.today()
    start_date = end_date - timedelta(days=6)
    
    daily_water = []
    for i in range(7):
        day = start_date + timedelta(days=i)
        amount = db.session.query(func.sum(Water.amount)).filter(
            Water.user_id == current_user.id,
            Water.date == day
        ).scalar() or 0
        daily_water.append({
            'date': day.strftime('%Y-%m-%d'),
            'day': day.strftime('%a'),
            'amount': amount
        })
    
    return render_template(
        'water.html',
        water_entries=water_entries,
        selected_date=selected_date,
        total_water=total_water,
        water_goal=water_goal,
        daily_water=daily_water,
        timedelta=timedelta
    )

@app.route('/exercise', methods=['GET', 'POST'])
@login_required
def exercise():
    if request.method == 'POST':
        activity = request.form.get('activity')
        duration = request.form.get('duration', type=int)
        calories_burned = request.form.get('calories_burned', type=int)
        notes = request.form.get('notes', '')
        exercise_date = request.form.get('date')
        
        try:
            exercise_date = datetime.strptime(exercise_date, '%Y-%m-%d').date()
        except:
            exercise_date = date.today()
        
        exercise_entry = Exercise(
            user_id=current_user.id,
            date=exercise_date,
            activity=activity,
            duration=duration,
            calories_burned=calories_burned,
            notes=notes
        )
        
        db.session.add(exercise_entry)
        db.session.commit()
        flash('Exercise added successfully', 'success')
        return redirect(url_for('exercise'))
    
    # Get exercise entries for today by default
    today = date.today()
    selected_date = request.args.get('date', today.strftime('%Y-%m-%d'))
    
    try:
        selected_date = datetime.strptime(selected_date, '%Y-%m-%d').date()
    except:
        selected_date = today
    
    exercise_entries = Exercise.query.filter_by(
        user_id=current_user.id,
        date=selected_date
    ).order_by(Exercise.created_at).all()
    
    # Calculate totals
    total_duration = sum(entry.duration for entry in exercise_entries)
    total_calories_burned = sum(entry.calories_burned or 0 for entry in exercise_entries)
    
    # Get data for the last 7 days
    end_date = date.today()
    start_date = end_date - timedelta(days=6)
    
    daily_exercise = []
    for i in range(7):
        day = start_date + timedelta(days=i)
        duration = db.session.query(func.sum(Exercise.duration)).filter(
            Exercise.user_id == current_user.id,
            Exercise.date == day
        ).scalar() or 0
        calories = db.session.query(func.sum(Exercise.calories_burned)).filter(
            Exercise.user_id == current_user.id,
            Exercise.date == day
        ).scalar() or 0
        daily_exercise.append({
            'date': day.strftime('%Y-%m-%d'),
            'day': day.strftime('%a'),
            'duration': duration,
            'calories': calories
        })
    
    return render_template(
        'exercise.html',
        exercise_entries=exercise_entries,
        selected_date=selected_date,
        total_duration=total_duration,
        total_calories_burned=total_calories_burned,
        daily_exercise=daily_exercise,
        timedelta=timedelta
    )

@app.route('/mood', methods=['GET', 'POST'])
@login_required
def mood():
    if request.method == 'POST':
        mood_level = request.form.get('mood_level', type=int)
        mood_description = request.form.get('mood_description')
        notes = request.form.get('notes', '')
        mood_date = request.form.get('date')
        
        try:
            mood_date = datetime.strptime(mood_date, '%Y-%m-%d').date()
        except:
            mood_date = date.today()
        
        # Check if there's already a mood entry for this date
        existing = Mood.query.filter_by(
            user_id=current_user.id,
            date=mood_date
        ).first()
        
        if existing:
            existing.mood_level = mood_level
            existing.mood_description = mood_description
            existing.notes = notes
            flash('Mood updated successfully', 'success')
        else:
            mood_entry = Mood(
                user_id=current_user.id,
                date=mood_date,
                mood_level=mood_level,
                mood_description=mood_description,
                notes=notes
            )
            db.session.add(mood_entry)
            flash('Mood entry added successfully', 'success')
        
        db.session.commit()
        return redirect(url_for('mood'))
    
    # Get mood entries
    moods = Mood.query.filter_by(
        user_id=current_user.id
    ).order_by(Mood.date.desc()).limit(30).all()
    
    # Prepare data for chart
    mood_dates = [entry.date.strftime('%Y-%m-%d') for entry in moods]
    mood_levels = [entry.mood_level for entry in moods]
    
    # Reverse lists to show oldest to newest
    mood_dates.reverse()
    mood_levels.reverse()
    
    return render_template(
        'mood.html',
        moods=moods,
        mood_dates=json.dumps(mood_dates),
        mood_levels=json.dumps(mood_levels)
    )

@app.route('/reminders', methods=['GET', 'POST'])
@login_required
def reminders():
    if request.method == 'POST':
        reminder_type = request.form.get('reminder_type')
        time = request.form.get('time')
        days = request.form.getlist('days')
        message = request.form.get('message', '')
        
        days_str = ','.join(days)
        
        try:
            time_obj = datetime.strptime(time, '%H:%M').time()
        except:
            flash('Invalid time format', 'danger')
            return redirect(url_for('reminders'))
        
        reminder = Reminder(
            user_id=current_user.id,
            reminder_type=reminder_type,
            time=time_obj,
            days=days_str,
            message=message,
            active=True
        )
        
        db.session.add(reminder)
        db.session.commit()
        flash('Reminder added successfully', 'success')
        return redirect(url_for('reminders'))
    
    # Get reminders
    reminders = Reminder.query.filter_by(
        user_id=current_user.id,
        active=True
    ).order_by(Reminder.time).all()
    
    return render_template('reminders.html', reminders=reminders)

@app.route('/reports')
@login_required
def reports():
    # Get date range
    end_date = date.today()
    start_date = end_date - timedelta(days=29)  # Last 30 days including today
    
    # Get weight data
    dates, weights = get_weight_data(current_user.id, days=30)
    
    # Get daily calories and water intake
    daily_calories = []
    daily_water = []
    daily_exercise = []
    
    for i in range(30):
        day = start_date + timedelta(days=i)
        
        calories = get_total_calories_for_date(current_user.id, day)
        water = get_water_for_date(current_user.id, day)
        calories_burned = get_calories_burned_for_date(current_user.id, day)
        
        daily_calories.append({
            'date': day.strftime('%Y-%m-%d'),
            'day': day.strftime('%a'),
            'value': calories
        })
        
        daily_water.append({
            'date': day.strftime('%Y-%m-%d'),
            'day': day.strftime('%a'),
            'value': water
        })
        
        daily_exercise.append({
            'date': day.strftime('%Y-%m-%d'),
            'day': day.strftime('%a'),
            'value': calories_burned
        })
    
    # Get user goals and profile
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    
    user_data = {
        'username': current_user.username,
        'name': profile.name if profile else '',
        'email': current_user.email,
        'age': profile.age if profile else '',
        'gender': profile.gender if profile else '',
        'height': profile.height if profile else '',
        'weight_goal': profile.weight_goal if profile else '',
        'calorie_goal': profile.calorie_goal if profile else 2000,
        'water_goal': profile.water_goal if profile else 2000,
        'fitness_goal': profile.fitness_goal if profile else ''
    }
    
    return render_template(
        'reports.html',
        user_data=user_data,
        dates=json.dumps(dates),
        weights=json.dumps(weights),
        daily_calories=daily_calories,
        daily_water=daily_water,
        daily_exercise=daily_exercise,
        start_date=start_date,
        end_date=end_date
    )

@app.route('/toggle_theme/<theme>')
@login_required
def toggle_theme(theme):
    valid_themes = ['green', 'blue', 'orange', 'purple']
    
    if theme in valid_themes:
        session['theme'] = theme
        
        # Update user profile
        profile = UserProfile.query.filter_by(user_id=current_user.id).first()
        if profile:
            profile.theme = theme
            db.session.commit()
    
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/api/chat', methods=['POST'])
@login_required
def chat_api():
    """AI Chatbot API endpoint"""
    data = request.get_json()
    
    if not data or 'message' not in data:
        return jsonify({'error': 'Message is required'}), 400
    
    user_message = data['message']
    
    # Get user's fitness and wellness data for context
    user_profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    
    # Get some user stats for context
    latest_weight = Weight.query.filter_by(user_id=current_user.id).order_by(Weight.date.desc()).first()
    weight_val = latest_weight.weight if latest_weight else None
    
    today_stats = get_today_stats(current_user.id)
    
    # Build context for the AI
    context = {
        'username': current_user.username,
        'fitness_goal': user_profile.fitness_goal if user_profile else None,
        'calorie_goal': user_profile.calorie_goal if user_profile else 2000,
        'water_goal': user_profile.water_goal if user_profile else 2000,
        'weight': weight_val,
        'weight_goal': user_profile.weight_goal if user_profile else None,
        'today_calories': today_stats['calories_consumed'],
        'today_water': today_stats['water_intake'],
        'today_calories_burned': today_stats['calories_burned']
    }
    
    # Construct system message with user context
    system_message = f"""
    You are a helpful fitness and wellness assistant for the WellnessXM365 app. 
    Your name is Wellness AI Assistant. You provide advice, motivation, and information 
    about fitness, nutrition, wellness, and health. Keep responses conversational, friendly,
    and relatively brief (under 200 words).
    
    Current user context:
    - Username: {context['username']}
    - Fitness goal: {context['fitness_goal'] or 'Not specified'}
    - Daily calorie goal: {context['calorie_goal']} calories
    - Daily water goal: {context['water_goal']} ml
    - Current weight: {context['weight'] or 'Not recorded'} kg
    - Weight goal: {context['weight_goal'] or 'Not specified'} kg
    - Today's calories consumed: {context['today_calories']} calories
    - Today's water intake: {context['today_water']} ml
    - Today's calories burned: {context['today_calories_burned']} calories
    
    If asked about features of the app, you can mention:
    - Tracking diet and calories
    - Monitoring water intake
    - Recording weight progress
    - Logging exercise activities
    - Tracking mood
    - Setting reminders
    - Generating reports
    
    Never invent data that isn't provided in the context. If you don't know something about 
    the user, acknowledge that the information isn't available and suggest how they might 
    track or add that data in the app.
    """
    
    try:
        # Call OpenAI API with GPT-4o model
        response = openai_client.chat.completions.create(
            model="gpt-4o",  # the newest OpenAI model is "gpt-4o" which was released May 13, 2024.
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        # Extract the assistant's reply
        assistant_reply = response.choices[0].message.content
        
        return jsonify({
            'response': assistant_reply,
            'reply': assistant_reply
        })
        
    except Exception as e:
        logging.error(f"Error calling OpenAI API: {str(e)}")
        return jsonify({
            'error': 'Something went wrong with the AI assistant. Please try again later.',
            'details': str(e)
        }), 500

@app.route('/delete_entry', methods=['POST'])
@login_required
def delete_entry():
    entry_type = request.form.get('entry_type')
    entry_id = request.form.get('entry_id', type=int)
    
    if not entry_type or not entry_id:
        flash('Invalid request', 'danger')
        return redirect(request.referrer or url_for('dashboard'))
    
    if entry_type == 'diet':
        entry = Diet.query.get(entry_id)
    elif entry_type == 'weight':
        entry = Weight.query.get(entry_id)
    elif entry_type == 'water':
        entry = Water.query.get(entry_id)
    elif entry_type == 'exercise':
        entry = Exercise.query.get(entry_id)
    elif entry_type == 'mood':
        entry = Mood.query.get(entry_id)
    elif entry_type == 'reminder':
        entry = Reminder.query.get(entry_id)
    else:
        flash('Invalid entry type', 'danger')
        return redirect(request.referrer or url_for('dashboard'))
    
    if not entry or entry.user_id != current_user.id:
        flash('Entry not found or access denied', 'danger')
        return redirect(request.referrer or url_for('dashboard'))
    
    db.session.delete(entry)
    db.session.commit()
    flash('Entry deleted successfully', 'success')
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/api/export_data')
@login_required
def export_data():
    # Create a JSON object with all user data
    user_data = {
        'profile': {},
        'diet': [],
        'weight': [],
        'water': [],
        'exercise': [],
        'mood': [],
        'reminders': []
    }
    
    # Get profile
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if profile:
        user_data['profile'] = {
            'name': profile.name,
            'age': profile.age,
            'gender': profile.gender,
            'height': profile.height,
            'weight_goal': profile.weight_goal,
            'calorie_goal': profile.calorie_goal,
            'water_goal': profile.water_goal,
            'theme': profile.theme,
            'fitness_goal': profile.fitness_goal
        }
    
    # Get diet entries
    diet_entries = Diet.query.filter_by(user_id=current_user.id).all()
    for entry in diet_entries:
        user_data['diet'].append({
            'date': entry.date.strftime('%Y-%m-%d'),
            'meal_type': entry.meal_type,
            'food_name': entry.food_name,
            'calories': entry.calories,
            'carbs': entry.carbs,
            'protein': entry.protein,
            'fat': entry.fat
        })
    
    # Get weight entries
    weight_entries = Weight.query.filter_by(user_id=current_user.id).all()
    for entry in weight_entries:
        user_data['weight'].append({
            'date': entry.date.strftime('%Y-%m-%d'),
            'weight': entry.weight,
            'notes': entry.notes
        })
    
    # Get water entries
    water_entries = Water.query.filter_by(user_id=current_user.id).all()
    for entry in water_entries:
        user_data['water'].append({
            'date': entry.date.strftime('%Y-%m-%d'),
            'amount': entry.amount
        })
    
    # Get exercise entries
    exercise_entries = Exercise.query.filter_by(user_id=current_user.id).all()
    for entry in exercise_entries:
        user_data['exercise'].append({
            'date': entry.date.strftime('%Y-%m-%d'),
            'activity': entry.activity,
            'duration': entry.duration,
            'calories_burned': entry.calories_burned,
            'notes': entry.notes
        })
    
    # Get mood entries
    mood_entries = Mood.query.filter_by(user_id=current_user.id).all()
    for entry in mood_entries:
        user_data['mood'].append({
            'date': entry.date.strftime('%Y-%m-%d'),
            'mood_level': entry.mood_level,
            'mood_description': entry.mood_description,
            'notes': entry.notes
        })
    
    # Get reminders
    reminder_entries = Reminder.query.filter_by(user_id=current_user.id).all()
    for entry in reminder_entries:
        user_data['reminders'].append({
            'reminder_type': entry.reminder_type,
            'time': entry.time.strftime('%H:%M'),
            'days': entry.days,
            'message': entry.message,
            'active': entry.active
        })
    
    return jsonify(user_data)

# API endpoints for new features
@app.route('/api/user-progress')
@login_required
def get_user_progress():
    """API endpoint for loading screen to get user's wellness journey progress"""
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    
    # Get counts of different tracking entries
    diet_count = Diet.query.filter_by(user_id=current_user.id).count()
    water_count = Water.query.filter_by(user_id=current_user.id).count()
    weight_count = Weight.query.filter_by(user_id=current_user.id).count()
    exercise_count = Exercise.query.filter_by(user_id=current_user.id).count()
    mood_count = Mood.query.filter_by(user_id=current_user.id).count()
    
    # Get the user's current stats for today
    today = date.today()
    water_today = get_water_for_date(current_user.id, today)
    calories_today = get_total_calories_for_date(current_user.id, today)
    exercise_today = Exercise.query.filter_by(user_id=current_user.id, date=today).all()
    exercise_minutes = sum(e.duration for e in exercise_today) if exercise_today else 0
    
    # Calculate progress percentages
    water_goal = profile.water_goal if profile else 2000
    water_progress = min(100, int((water_today / water_goal) * 100)) if water_goal > 0 else 0
    
    calorie_goal = profile.calorie_goal if profile else 2000
    calorie_progress = min(100, int((calories_today / calorie_goal) * 100)) if calorie_goal > 0 else 0
    
    # Get latest mood
    latest_mood = Mood.query.filter_by(user_id=current_user.id).order_by(Mood.date.desc()).first()
    mood_description = latest_mood.mood_description if latest_mood else "Unknown"
    
    return jsonify({
        'username': current_user.username,
        'journey_stage': 'beginner' if sum([diet_count, water_count, weight_count, exercise_count, mood_count]) < 50 else 'intermediate',
        'stats': {
            'water_progress': water_progress,
            'calorie_progress': calorie_progress,
            'exercise_minutes': exercise_minutes,
            'current_mood': mood_description
        },
        'tracking_counts': {
            'diet': diet_count,
            'water': water_count,
            'weight': weight_count,
            'exercise': exercise_count,
            'mood': mood_count
        }
    })

@app.route('/api/progress-summary')
@login_required
def get_progress_summary():
    """API endpoint for voice commands to get a summary of user's progress"""
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    
    # Get the user's current stats for today
    today = date.today()
    water_today = get_water_for_date(current_user.id, today)
    calories_today = get_total_calories_for_date(current_user.id, today)
    exercise_today = Exercise.query.filter_by(user_id=current_user.id, date=today).all()
    exercise_minutes = sum(e.duration for e in exercise_today) if exercise_today else 0
    calories_burned = sum(e.calories_burned for e in exercise_today if e.calories_burned) if exercise_today else 0
    
    # Calculate progress percentages
    water_goal = profile.water_goal if profile else 2000
    water_progress = min(100, int((water_today / water_goal) * 100)) if water_goal > 0 else 0
    
    calorie_goal = profile.calorie_goal if profile else 2000
    calorie_progress = min(100, int((calories_today / calorie_goal) * 100)) if calorie_goal > 0 else 0
    
    # Get latest mood
    latest_mood = Mood.query.filter_by(user_id=current_user.id).order_by(Mood.date.desc()).first()
    mood_description = latest_mood.mood_description if latest_mood else "Unknown"
    
    # Get weight progress
    weight_entries = Weight.query.filter_by(user_id=current_user.id).order_by(Weight.date).all()
    weight_today = weight_entries[-1].weight if weight_entries else 0
    weight_goal = profile.weight_goal if profile else 0
    
    # Calculate weight progress if goal exists
    weight_progress = 0
    if weight_goal > 0 and weight_entries and len(weight_entries) > 1:
        initial_weight = weight_entries[0].weight
        if initial_weight > weight_goal:  # Weight loss goal
            total_to_lose = initial_weight - weight_goal
            lost_so_far = initial_weight - weight_today
            weight_progress = min(100, int((lost_so_far / total_to_lose) * 100)) if total_to_lose > 0 else 0
        elif initial_weight < weight_goal:  # Weight gain goal
            total_to_gain = weight_goal - initial_weight
            gained_so_far = weight_today - initial_weight
            weight_progress = min(100, int((gained_so_far / total_to_gain) * 100)) if total_to_gain > 0 else 0
    
    return jsonify({
        'stats': {
            'water_progress': water_progress,
            'calorie_progress': calorie_progress,
            'exercise_minutes': exercise_minutes,
            'calories_burned': calories_burned,
            'current_mood': mood_description,
            'weight_progress': weight_progress
        }
    })

@app.route('/api/achievements', methods=['GET', 'POST'])
@login_required
def manage_achievements():
    """API endpoint for the gamified achievement system"""
    check_new = request.args.get('check_new', False)
    
    if check_new:
        # Logic to check for new achievements would go here
        # For now, return a sample response
        return jsonify({
            'has_new': False,
            'new_achievement': None
        })
    
    # For demo purposes, return sample achievements data
    sample_achievements = [
        {
            'name': "First Step",
            'description': "Record your first weight entry",
            'icon': "trending-up",
            'unlocked': True,
            'date': "May 1, 2025",
            'recent': False
        },
        {
            'name': "Hydration Hero",
            'description': "Reach your daily water goal for 7 consecutive days",
            'icon': "droplet",
            'unlocked': True,
            'date': "May 3, 2025",
            'recent': True
        },
        {
            'name': "Consistency Champion",
            'description': "Log your meals every day for 2 weeks",
            'icon': "calendar",
            'unlocked': False,
            'progress': 43
        },
        {
            'name': "Exercise Expert",
            'description': "Record 10 different types of exercises",
            'icon': "activity",
            'unlocked': False,
            'progress': 60
        },
        {
            'name': "Goal Getter",
            'description': "Reach your weight goal",
            'icon': "target",
            'unlocked': False,
            'progress': 25
        },
        {
            'name': "Mindfulness Master",
            'description': "Log your mood for 30 consecutive days",
            'icon': "smile",
            'unlocked': False,
            'progress': 10
        }
    ]
    
    return jsonify({
        'achievements': sample_achievements
    })

@app.route('/api/tooltips/<metric_id>')
@login_required
def get_tooltip_data(metric_id):
    """API endpoint for interactive tooltips to get detailed information on metrics"""
    # This would typically fetch more detailed data from the database or an external source
    # For now, we'll return a basic structure with information on the requested metric
    
    tooltip_data = {
        'title': metric_id.replace('_', ' ').title(),
        'basic_info': 'Information about this metric is being loaded...',
        'detailed_info': [],
        'has_chart': False
    }
    
    # Customize based on the metric
    if metric_id == 'calories':
        tooltip_data['basic_info'] = 'Calories are a measure of energy from food and drink.'
        tooltip_data['detailed_info'] = [
            'The average adult needs 1,600-3,000 calories per day.',
            'Your specific needs depend on age, gender, height, weight, and activity level.',
            'A calorie deficit is needed for weight loss, while a surplus is needed for weight gain.'
        ]
        tooltip_data['has_chart'] = True
    elif metric_id == 'water_intake':
        tooltip_data['basic_info'] = 'Proper hydration is essential for overall health and wellness.'
        tooltip_data['detailed_info'] = [
            'The recommended daily water intake is about 3.7L for men and 2.7L for women.',
            'Water needs increase with exercise, hot weather, and during illness.',
            'Hydration supports digestion, circulation, and temperature regulation.'
        ]
    
    return jsonify(tooltip_data)
# Google OAuth config
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
GOOGLE_AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'

@app.route('/login/google')
def google_login():
    google = OAuth2Session(
        GOOGLE_CLIENT_ID,
        scope=['openid', 'email', 'profile'],
        redirect_uri=url_for('google_callback', _external=True)
    )
    authorization_url, state = google.authorization_url(GOOGLE_AUTHORIZE_URL)
    session['oauth_state'] = state
    return redirect(authorization_url)

@app.route('/login/google/callback')
def google_callback():
    google = OAuth2Session(
        GOOGLE_CLIENT_ID,
        state=session.get('oauth_state'),
        redirect_uri=url_for('google_callback', _external=True)
    )
    
    try:
        token = google.fetch_token(
            GOOGLE_TOKEN_URL,
            client_secret=GOOGLE_CLIENT_SECRET,
            authorization_response=request.url
        )
        
        userinfo = google.get(GOOGLE_USERINFO_URL).json()
        email = userinfo.get('email')
        
        # Check if user exists
        user = User.query.filter_by(email=email).first()
        if not user:
            # Create new user
            username = email.split('@')[0]
            user = User(username=username, email=email)
            user.set_password(os.urandom(24).hex())  # Random secure password
            db.session.add(user)
            
            # Create user profile
            profile = UserProfile(
                user=user,
                name=userinfo.get('name', ''),
                theme='orange',
                calorie_goal=2000,
                water_goal=2000
            )
            db.session.add(profile)
            db.session.commit()
        
        login_user(user)
        session['theme'] = user.profile.theme if user.profile else 'orange'
        flash('Successfully logged in with Google!', 'success')
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        flash('Failed to log in with Google. Please try again.', 'danger')
        return redirect(url_for('login'))
