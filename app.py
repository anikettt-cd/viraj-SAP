import os
import re
import pandas as pd
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your_super_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def init_db():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def parse_material_description(desc):
    """
    Industrial parsing engine tailored for Viraj Profiles Limited.
    Extracts Steel Grade, Shape Profile, and Size Dimensions from raw SAP strings.
    """
    desc_upper = str(desc).upper()
    
    # Extract Grade
    grade_match = re.search(r'\b(304L|316L|304|316|410|420|430|201|202)\b', desc_upper)
    grade = grade_match.group(0) if grade_match else "OTHER"
    
    # Extract Shape/Profile
    profile = "OTHER"
    if any(k in desc_upper for k in ["WIRE ROD", "ROD", "WIRE"]):
        profile = "WIRE ROD"
    elif any(k in desc_upper for k in ["BRIGHT BAR", "BAR", "ROUND BAR"]):
        profile = "BRIGHT BAR"
    elif "FLANGE" in desc_upper:
        profile = "FLANGE"
    elif any(k in desc_upper for k in ["FASTENER", "BOLT", "NUT", "SCREW"]):
        profile = "FASTENER"
    elif "PROFILE" in desc_upper:
        profile = "PROFILE SECTIONS"

    # Extract Size
    size_match = re.search(r'(\d+(\.\d+)?)\s*(MM|M)\b', desc_upper)
    size = size_match.group(0) if size_match else "N/A"
    
    return {
        "extracted_grade": grade,
        "extracted_profile": profile,
        "extracted_size": size
    }

# Updated to support Apple native file formatting structures along with Excel standards
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'numbers'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_multiple_files(file_paths, query=None):
    """Loads multiple Excel/Numbers files, runs the smart parser, and combines them cleanly"""
    combined_frames = []
    
    for path in file_paths:
        if not os.path.exists(path):
            continue
        try:
            # Check for Apple Numbers format framework
            if path.endswith('.numbers'):
                from numbers_parser import Document
                doc = Document(path)
                sheets = doc.sheets()
                tables = sheets[0].tables()
                data = tables[0].rows(as_list=True)
                
                # Turn list architecture into string-mapped DataFrame
                if data and len(data) > 1:
                    df = pd.DataFrame(data[1:], columns=data[0]).astype(str)
                else:
                    df = pd.DataFrame()
            else:
                # Load standard excel sheets as strict searchable text strings
                df = pd.read_excel(path, dtype=str)
                
            if df.empty:
                continue
                
            df = df.fillna('')
            
            # Identify Description Column
            target_col = None
            for col in df.columns:
                if 'description' in col.lower():
                    target_col = col
                    break
                    
            parsed_data = []
            for _, row in df.iterrows():
                row_dict = row.to_dict()
                if target_col and row_dict.get(target_col):
                    specs = parse_material_description(row_dict[target_col])
                else:
                    specs = {"extracted_grade": "N/A", "extracted_profile": "N/A", "extracted_size": "N/A"}
                row_dict.update(specs)
                parsed_data.append(row_dict)
                
            combined_frames.append(pd.DataFrame(parsed_data))
        except Exception as e:
            print(f"--> Error loading file path {path}: {str(e)}")
        
    if not combined_frames:
        return pd.DataFrame()
        
    # Combine all spreadsheets together into one master matrix
    master_df = pd.concat(combined_frames, ignore_index=True, sort=False)
    master_df = master_df.fillna('')
    
    # ⚡ SMART TOKENIZED SEARCH ALGORITHM ⚡
    if query:
        # Break input query into individual space-separated words (tokens)
        tokens = query.split()
        
        # Start with a mask where all rows are checked as matching True
        final_mask = pd.Series(True, index=master_df.index)
        
        # For every single word, verify it exists anywhere inside the row values
        for token in tokens:
            token_mask = master_df.astype(str).apply(
                lambda x: x.str.contains(re.escape(token), case=False)
            ).any(axis=1)
            
            # Chain the conditions together (Logical AND operation)
            final_mask = final_mask & token_mask
            
        master_df = master_df[final_mask]
        
    return master_df

@app.route('/')
def home():
    return redirect('/login')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_pw = generate_password_hash(password)
        try:
            conn = sqlite3.connect('users.db')
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_pw))
            conn.commit()
            return redirect('/login')
        except sqlite3.IntegrityError:
            return "Username already exists!"
        finally:
            conn.close()
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()
        if user and check_password_hash(user[2], password):
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['uploaded_file_paths'] = []
            session['uploaded_filenames'] = []
            return redirect('/dashboard')
        else:
            return "Invalid login credentials."
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/clear-files')
def clear_files():
    session['uploaded_file_paths'] = []
    session['uploaded_filenames'] = []
    return redirect('/dashboard')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_id' not in session:
        return redirect('/login')
        
    if 'uploaded_file_paths' not in session:
        session['uploaded_file_paths'] = []
        session['uploaded_filenames'] = []
        
    columns = []
    rows = []
    
    if request.method == 'POST':
        uploaded_files = request.files.getlist('file')
        paths_pool = list(session['uploaded_file_paths'])
        names_pool = list(session['uploaded_filenames'])
        
        for file in uploaded_files:
            if file and file.filename != '' and allowed_file(file.filename):
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
                file.save(file_path)
                
                if file_path not in paths_pool:
                    paths_pool.append(file_path)
                    names_pool.append(file.filename)
                    
        session['uploaded_file_paths'] = paths_pool
        session['uploaded_filenames'] = names_pool

    if session['uploaded_file_paths']:
        try:
            master_df = process_multiple_files(session['uploaded_file_paths'])
            if not master_df.empty:
                columns = list(master_df.columns)
                rows = master_df.to_dict(orient='records')
        except Exception as e:
            print(f"--> Combined file processing error: {str(e)}")

    return render_template('dashboard.html', username=session['username'], columns=columns, rows=rows, current_filenames=session['uploaded_filenames'])

@app.route('/search', methods=['POST'])
def search_data():
    if 'user_id' not in session or not session.get('uploaded_file_paths'):
        return redirect('/dashboard')
        
    query = request.form.get('query', '').strip()
    
    try:
        master_df = process_multiple_files(session['uploaded_file_paths'], query=query)
        columns = list(master_df.columns) if not master_df.empty else []
        rows = master_df.to_dict(orient='records') if not master_df.empty else []
        return render_template('dashboard.html', username=session['username'], columns=columns, rows=rows, query=query, current_filenames=session['uploaded_filenames'])
    except Exception as e:
        return f"Error filtering master database grid pool: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True)