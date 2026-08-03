import os
import uuid
from datetime import date
from functools import wraps

import pymysql
import pymysql.cursors
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')


# ---------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------
def get_db():
    return pymysql.connect(
        host=os.environ.get('MYSQLHOST', 'localhost'),
        user=os.environ.get('MYSQLUSER', 'root'),
        password=os.environ.get('MYSQLPASSWORD', 'root'),
        database=os.environ.get('MYSQLDATABASE', 'ikai_production'),
        port=int(os.environ.get('MYSQLPORT', 3306)),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# ---------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('role') not in roles:
                flash("You don't have access to that page.", "error")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return wrapper
    return decorator


def admin_only_action(redirect_endpoint, **redirect_kwargs):
    """Guard for the POST/write branch of a route that is otherwise viewable
    by more than one role (e.g. store users can view Raw Materials / BOM,
    but only admins can add or edit). Flashes an error and redirects if the
    current session isn't an admin."""
    if session.get('role') != 'admin':
        flash("You don't have permission to make changes here. Viewing only.", "error")
        return redirect(url_for(redirect_endpoint, **redirect_kwargs))
    return None


# ---------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------
@app.route('/')
@login_required
def index():
    if session['role'] == 'admin':
        return redirect(url_for('admin_dashboard'))
    return redirect(url_for('warehouse_dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        db = get_db()
        try:
            with db.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE username=%s AND is_active=1", (username,))
                user = cur.fetchone()
            if user and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['full_name'] = user['full_name']
                return redirect(url_for('index'))
            flash('Invalid username or password.', 'error')
        finally:
            db.close()
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------
@app.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) c FROM raw_materials")
            rm_count = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) c FROM finished_products")
            fp_count = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) c FROM raw_material_receipts WHERE qc_status='pending'")
            pending_qc = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) c FROM raw_material_orders WHERE status IN ('pending','partially_received')")
            open_orders = cur.fetchone()['c']
            cur.execute("SELECT * FROM raw_materials WHERE current_stock <= reorder_level ORDER BY current_stock ASC")
            low_stock = cur.fetchall()
            cur.execute("""SELECT pb.*, fp.name AS product_name, fp.unit FROM production_batches pb
                            JOIN finished_products fp ON fp.id = pb.finished_product_id
                            ORDER BY pb.created_at DESC LIMIT 8""")
            recent_production = cur.fetchall()
    finally:
        db.close()
    return render_template('admin_dashboard.html', rm_count=rm_count, fp_count=fp_count,
                            pending_qc=pending_qc, open_orders=open_orders,
                            low_stock=low_stock, recent_production=recent_production)


@app.route('/warehouse/dashboard')
@role_required('store', 'admin')
def warehouse_dashboard():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT COUNT(*) c FROM raw_material_receipts WHERE qc_status='pending'")
            pending_qc = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) c FROM raw_material_orders WHERE status IN ('pending','partially_received')")
            open_orders = cur.fetchone()['c']
            cur.execute("""SELECT pb.*, fp.name AS product_name, fp.unit FROM production_batches pb
                            JOIN finished_products fp ON fp.id = pb.finished_product_id
                            ORDER BY pb.created_at DESC LIMIT 6""")
            recent_production = cur.fetchall()
            cur.execute("SELECT * FROM raw_materials WHERE current_stock <= reorder_level ORDER BY current_stock ASC")
            low_stock = cur.fetchall()
    finally:
        db.close()
    return render_template('warehouse_dashboard.html', pending_qc=pending_qc, open_orders=open_orders,
                            recent_production=recent_production, low_stock=low_stock)


# ---------------------------------------------------------------------
# Masters: vendors, raw materials, finished products
#   - vendors & finished products: admin only (unchanged)
#   - raw materials: admin can add/edit; store can VIEW ONLY
# ---------------------------------------------------------------------
@app.route('/vendors', methods=['GET', 'POST'])
@role_required('admin')
def vendors():
    db = get_db()
    try:
        if request.method == 'POST':
            with db.cursor() as cur:
                cur.execute("""INSERT INTO vendors (name, contact_person, phone, email, address)
                                VALUES (%s,%s,%s,%s,%s)""",
                            (request.form['name'].strip(), request.form.get('contact_person', '').strip(),
                             request.form.get('phone', '').strip(), request.form.get('email', '').strip(),
                             request.form.get('address', '').strip()))
            db.commit()
            flash('Vendor added.', 'success')
            return redirect(url_for('vendors'))
        with db.cursor() as cur:
            cur.execute("SELECT * FROM vendors ORDER BY name")
            vendor_list = cur.fetchall()
    finally:
        db.close()
    return render_template('vendors.html', vendors=vendor_list)


# ---------------------------------------------------------------------
# Add this route right after vendors()
# ---------------------------------------------------------------------
@app.route('/vendors/<int:vendor_id>/delete', methods=['POST'])
@role_required('admin')
def delete_vendor(vendor_id):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM vendors WHERE id=%s FOR UPDATE", (vendor_id,))
            v = cur.fetchone()
            if not v:
                db.rollback()
                flash('Vendor not found.', 'error')
                return redirect(url_for('vendors'))

            cur.execute("SELECT COUNT(*) c FROM raw_material_orders WHERE vendor_id=%s", (vendor_id,))
            order_count = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) c FROM raw_material_receipts WHERE vendor_id=%s", (vendor_id,))
            receipt_count = cur.fetchone()['c']
            if order_count > 0 or receipt_count > 0:
                db.rollback()
                flash(f'Cannot delete "{v["name"]}" \u2014 it has {order_count} order item(s) and '
                      f'{receipt_count} receipt(s) linked to it. Delete those first.', 'error')
                return redirect(url_for('vendors'))

            cur.execute("DELETE FROM vendors WHERE id=%s", (vendor_id,))
        db.commit()
        flash(f'Vendor "{v["name"]}" deleted.', 'success')
    finally:
        db.close()
    return redirect(url_for('vendors'))


# ---------------------------------------------------------------------
# Add this route right after cancel_order_group()
# ---------------------------------------------------------------------
@app.route('/orders/<int:order_id>/delete', methods=['POST'])
@role_required('admin')
def delete_order(order_id):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM raw_material_orders WHERE id=%s FOR UPDATE", (order_id,))
            o = cur.fetchone()
            if not o:
                db.rollback()
                flash('Order item not found.', 'error')
                return redirect(url_for('orders'))

            cur.execute("SELECT COUNT(*) c FROM raw_material_receipts WHERE order_id=%s", (order_id,))
            if cur.fetchone()['c'] > 0:
                db.rollback()
                flash('This order has receipt(s) linked to it. Delete those receipts first '
                      '(from the Receive Stock page) before deleting the order.', 'error')
                return redirect(url_for('orders'))

            cur.execute("DELETE FROM raw_material_orders WHERE id=%s", (order_id,))
        db.commit()
        flash('Order item deleted.', 'success')
    finally:
        db.close()
    return redirect(url_for('orders'))


# ---------------------------------------------------------------------
# Add this route right after qc()
# Reverses everything a QC-passed receipt did: subtracts the qty back
# out of current_stock, removes the stock ledger entry it created,
# removes the QC check record, deletes the receipt itself, and
# recalculates the linked order's status.
# ---------------------------------------------------------------------
@app.route('/receive/<int:receipt_id>/delete', methods=['POST'])
@role_required('admin')
def delete_receipt(receipt_id):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM raw_material_receipts WHERE id=%s FOR UPDATE", (receipt_id,))
            receipt = cur.fetchone()
            if not receipt:
                db.rollback()
                flash('Receipt not found.', 'error')
                return redirect(url_for('receive'))

            cur.execute("SELECT COUNT(*) c FROM production_consumption WHERE raw_material_receipt_id=%s",
                        (receipt_id,))
            if cur.fetchone()['c'] > 0:
                db.rollback()
                flash('This batch has already been used in production and can\'t be deleted. '
                      'Delete the production batch(es) that used it first.', 'error')
                return redirect(url_for('receive'))

            if receipt['qc_status'] == 'passed':
                cur.execute("SELECT current_stock FROM raw_materials WHERE id=%s FOR UPDATE",
                            (receipt['raw_material_id'],))
                rm = cur.fetchone()
                if rm:
                    new_stock = float(rm['current_stock']) - float(receipt['qty_received'])
                    cur.execute("UPDATE raw_materials SET current_stock=%s WHERE id=%s",
                                (new_stock, receipt['raw_material_id']))

            cur.execute("""DELETE FROM stock_ledger WHERE item_type='raw_material'
                            AND reference_type='qc_pass' AND reference_id=%s""", (receipt_id,))
            # Delete per-criteria breakdown rows before the qc_checks row they point to
            cur.execute("""DELETE qci FROM qc_check_items qci
                            JOIN qc_checks qc ON qc.id = qci.qc_check_id
                            WHERE qc.receipt_id=%s""", (receipt_id,))
            cur.execute("DELETE FROM qc_checks WHERE receipt_id=%s", (receipt_id,))
            cur.execute("DELETE FROM raw_material_receipts WHERE id=%s", (receipt_id,))

            if receipt['order_id']:
                cur.execute("""SELECT qty_ordered,
                                (SELECT COALESCE(SUM(qty_received),0) FROM raw_material_receipts
                                 WHERE order_id=%s) AS total_received
                                FROM raw_material_orders WHERE id=%s""",
                            (receipt['order_id'], receipt['order_id']))
                r2 = cur.fetchone()
                if r2:
                    if r2['total_received'] <= 0:
                        new_status = 'pending'
                    elif r2['total_received'] >= r2['qty_ordered']:
                        new_status = 'received'
                    else:
                        new_status = 'partially_received'
                    cur.execute("UPDATE raw_material_orders SET status=%s WHERE id=%s",
                                (new_status, receipt['order_id']))
        db.commit()
        flash('Receipt deleted and stock adjusted back.', 'success')
    finally:
        db.close()
    return redirect(url_for('receive'))


@app.route('/raw-materials', methods=['GET', 'POST'])
@role_required('admin', 'store')
def raw_materials():
    db = get_db()
    try:
        if request.method == 'POST':
            # Store users can view this page but cannot add/edit raw materials.
            guard = admin_only_action('raw_materials')
            if guard:
                return guard

            category = request.form.get('category', '').strip() or 'raw_commodity'
            with db.cursor() as cur:
                cur.execute("""INSERT INTO raw_materials (name, unit, category, reorder_level, current_stock)
                                VALUES (%s,%s,%s,%s,0)""",
                            (request.form['name'].strip(), request.form['unit'].strip(),
                             category, float(request.form.get('reorder_level') or 0)))
            db.commit()
            flash('Raw material added.', 'success')
            return redirect(url_for('raw_materials'))

        with db.cursor() as cur:
            # NULL categories (if any) are grouped last, alphabetically within group
            cur.execute("""SELECT * FROM raw_materials
                            ORDER BY (category IS NULL), category, name""")
            materials = cur.fetchall()
            cur.execute("""SELECT DISTINCT category FROM raw_materials
                            WHERE category IS NOT NULL ORDER BY category""")
            all_categories = [r['category'] for r in cur.fetchall()]
    finally:
        db.close()

    # Fold "_legacy" categories into their base category for grouping/filtering,
    # but keep a flag on each row so the template can badge it.
    for m in materials:
        raw_cat = m['category'] or 'uncategorized'
        base = raw_cat[:-7] if raw_cat.endswith('_legacy') else raw_cat
        m['display_category'] = base
        m['is_legacy'] = raw_cat.endswith('_legacy')

    display_categories = sorted({m['display_category'] for m in materials})

    return render_template('raw_materials.html', materials=materials,
                            categories=display_categories, all_categories=all_categories,
                            can_edit=(session.get('role') == 'admin'))


@app.route('/raw-materials/<int:material_id>/adjust-stock', methods=['POST'])
@role_required('admin')
def adjust_raw_material_stock(material_id):
    direction = request.form.get('direction')
    qty = request.form.get('qty', type=float)
    reason = request.form.get('reason', '').strip()

    if direction not in ('in', 'out'):
        flash('Invalid adjustment direction.', 'error')
        return redirect(url_for('raw_materials'))
    if not qty or qty <= 0:
        flash('Enter a quantity greater than zero.', 'error')
        return redirect(url_for('raw_materials'))

    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM raw_materials WHERE id=%s FOR UPDATE", (material_id,))
            material = cur.fetchone()
            if not material:
                db.rollback()
                flash('Raw material not found.', 'error')
                return redirect(url_for('raw_materials'))

            if direction == 'out' and qty > float(material['current_stock']):
                db.rollback()
                flash(f"Cannot remove {qty} {material['unit']} \u2014 only "
                      f"{material['current_stock']} {material['unit']} currently in stock.", 'error')
                return redirect(url_for('raw_materials'))

            delta = qty if direction == 'in' else -qty
            cur.execute("UPDATE raw_materials SET current_stock = current_stock + %s WHERE id=%s",
                        (delta, material_id))
            cur.execute("SELECT current_stock FROM raw_materials WHERE id=%s", (material_id,))
            new_balance = cur.fetchone()['current_stock']

            remarks = reason or ('Manual stock addition' if direction == 'in' else 'Manual stock removal')
            cur.execute("""INSERT INTO stock_ledger
                            (item_type, item_id, transaction_type, qty, reference_type, reference_id,
                             balance_after, remarks, created_by)
                            VALUES ('raw_material', %s, %s, %s, 'manual_adjustment', NULL, %s, %s, %s)""",
                        (material_id, direction, qty, new_balance, remarks, session['user_id']))
        db.commit()
        verb = 'added to' if direction == 'in' else 'removed from'
        flash(f"{qty} {material['unit']} {verb} {material['name']}.", 'success')
    finally:
        db.close()
    return redirect(url_for('raw_materials'))


@app.route('/finished-products', methods=['GET', 'POST'])
@role_required('admin')
def finished_products():
    db = get_db()
    try:
        if request.method == 'POST':
            with db.cursor() as cur:
                cur.execute("""INSERT INTO finished_products (name, unit, current_stock)
                                VALUES (%s,%s,0)""",
                            (request.form['name'].strip(), request.form['unit'].strip()))
            db.commit()
            flash('Finished product added.', 'success')
            return redirect(url_for('finished_products'))
        with db.cursor() as cur:
            cur.execute("SELECT * FROM finished_products ORDER BY name")
            products = cur.fetchall()
    finally:
        db.close()
    return render_template('finished_products.html', products=products)


# ---------------------------------------------------------------------
# Recipes (BOM): admin can add/edit/remove ingredients; store can VIEW ONLY
# ---------------------------------------------------------------------
@app.route('/bom', methods=['GET', 'POST'])
@role_required('admin', 'store')
def bom():
    db = get_db()
    try:
        if request.method == 'POST':
            # Store users can view recipes but cannot add/update ingredients.
            fp_id_for_redirect = request.form.get('finished_product_id', type=int)
            guard = admin_only_action('bom', finished_product_id=fp_id_for_redirect)
            if guard:
                return guard

            fp_id = int(request.form['finished_product_id'])
            rm_id = int(request.form['raw_material_id'])
            qty = float(request.form['qty_required'])
            with db.cursor() as cur:
                cur.execute("""INSERT INTO bom (finished_product_id, raw_material_id, qty_required)
                                VALUES (%s,%s,%s)
                                ON DUPLICATE KEY UPDATE qty_required=%s""", (fp_id, rm_id, qty, qty))
            db.commit()
            flash('Recipe updated.', 'success')
            return redirect(url_for('bom', finished_product_id=fp_id))

        selected_id = request.args.get('finished_product_id', type=int)
        with db.cursor() as cur:
            cur.execute("SELECT * FROM finished_products ORDER BY name")
            products = cur.fetchall()
            cur.execute("SELECT * FROM raw_materials ORDER BY name")
            materials = cur.fetchall()
            bom_rows = []
            if selected_id:
                cur.execute("""SELECT b.*, rm.name AS material_name, rm.unit FROM bom b
                                JOIN raw_materials rm ON rm.id = b.raw_material_id
                                WHERE b.finished_product_id=%s ORDER BY rm.name""", (selected_id,))
                bom_rows = cur.fetchall()
    finally:
        db.close()
    return render_template('bom.html', products=products, materials=materials,
                            selected_id=selected_id, bom_rows=bom_rows,
                            can_edit=(session.get('role') == 'admin'))


@app.route('/bom/<int:bom_id>/delete', methods=['POST'])
@role_required('admin')
def bom_delete(bom_id):
    fp_id = request.form.get('finished_product_id', type=int)
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("DELETE FROM bom WHERE id=%s", (bom_id,))
        db.commit()
        flash('Ingredient removed from recipe.', 'success')
    finally:
        db.close()
    return redirect(url_for('bom', finished_product_id=fp_id))


# ---------------------------------------------------------------------
# Orders -> Receive -> QC (warehouse + admin)
# ---------------------------------------------------------------------
@app.route('/orders', methods=['GET', 'POST'])
@role_required('admin', 'store')
def orders():
    db = get_db()
    try:
        if request.method == 'POST':
            vendor_id = int(request.form['vendor_id'])
            order_date = request.form['order_date']
            notes = request.form.get('notes', '')
            material_ids = request.form.getlist('raw_material_id[]')
            qtys = request.form.getlist('qty_ordered[]')

            items = []
            for mid, qty in zip(material_ids, qtys):
                if not mid or not qty:
                    continue
                qty_f = float(qty)
                if qty_f <= 0:
                    continue
                items.append((int(mid), qty_f))

            if not items:
                flash('Add at least one item with a valid quantity.', 'error')
                return redirect(url_for('orders'))

            group_id = uuid.uuid4().hex
            with db.cursor() as cur:
                for rm_id, qty_f in items:
                    cur.execute("""INSERT INTO raw_material_orders
                                    (order_group_id, raw_material_id, vendor_id, qty_ordered, order_date,
                                     ordered_by, notes)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                                (group_id, rm_id, vendor_id, qty_f, order_date, session['user_id'], notes))
            db.commit()
            flash(f'Order placed with {len(items)} item(s).', 'success')
            return redirect(url_for('orders'))

        with db.cursor() as cur:
            cur.execute("SELECT * FROM raw_materials ORDER BY name")
            materials = cur.fetchall()
            cur.execute("SELECT * FROM vendors ORDER BY name")
            vendor_list = cur.fetchall()
            cur.execute("""SELECT o.*, rm.name AS material_name, rm.unit, v.name AS vendor_name
                            FROM raw_material_orders o
                            JOIN raw_materials rm ON rm.id = o.raw_material_id
                            JOIN vendors v ON v.id = o.vendor_id
                            ORDER BY o.created_at DESC LIMIT 150""")
            order_rows = cur.fetchall()
    finally:
        db.close()

    # Group order lines placed together (same order_group_id) into one card.
    # Legacy orders from before this feature have no group id, so each gets
    # its own single-item group key.
    groups_by_key = {}
    order_groups = []
    for o in order_rows:
        key = o['order_group_id'] or f"single-{o['id']}"
        if key not in groups_by_key:
            g = {
                'group_key': key,
                'vendor_name': o['vendor_name'],
                'order_date': o['order_date'],
                'created_at': o['created_at'],
                'notes': o['notes'],
                'order_items': [],
            }
            groups_by_key[key] = g
            order_groups.append(g)
        groups_by_key[key]['order_items'].append(o)

    order_groups.sort(key=lambda g: g['created_at'], reverse=True)

    return render_template('orders.html', materials=materials, vendors=vendor_list,
                            order_groups=order_groups, today=date.today().isoformat())


@app.route('/orders/<int:order_id>/cancel', methods=['POST'])
@role_required('admin', 'store')
def cancel_order(order_id):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM raw_material_orders WHERE id=%s FOR UPDATE", (order_id,))
            o = cur.fetchone()
            if not o:
                db.rollback()
                flash('Order item not found.', 'error')
                return redirect(url_for('orders'))
            if o['status'] != 'pending':
                db.rollback()
                flash('Only items with nothing received yet can be cancelled.', 'error')
                return redirect(url_for('orders'))
            cur.execute("UPDATE raw_material_orders SET status='cancelled' WHERE id=%s", (order_id,))
        db.commit()
        flash('Order item cancelled.', 'success')
    finally:
        db.close()
    return redirect(url_for('orders'))


@app.route('/orders/group/<group_key>/cancel', methods=['POST'])
@role_required('admin', 'store')
def cancel_order_group(group_key):
    db = get_db()
    try:
        with db.cursor() as cur:
            if group_key.startswith('single-'):
                order_id = int(group_key.replace('single-', '', 1))
                cur.execute("SELECT id, status FROM raw_material_orders WHERE id=%s FOR UPDATE", (order_id,))
                row = cur.fetchone()
                rows = [row] if row else []
            else:
                cur.execute("""SELECT id, status FROM raw_material_orders
                                WHERE order_group_id=%s FOR UPDATE""", (group_key,))
                rows = cur.fetchall()

            if not rows:
                db.rollback()
                flash('Order not found.', 'error')
                return redirect(url_for('orders'))

            non_pending = [r for r in rows if r['status'] != 'pending']
            if non_pending:
                db.rollback()
                flash('Some items in this order have already been received, so it can\'t be bulk-cancelled. '
                      'Cancel the remaining pending items individually.', 'error')
                return redirect(url_for('orders'))

            ids = [r['id'] for r in rows]
            placeholders = ','.join(['%s'] * len(ids))
            cur.execute(f"UPDATE raw_material_orders SET status='cancelled' WHERE id IN ({placeholders})", ids)
        db.commit()
        flash('Order cancelled.', 'success')
    finally:
        db.close()
    return redirect(url_for('orders'))


@app.route('/receive', methods=['GET', 'POST'])
@role_required('admin', 'store')
def receive():
    db = get_db()
    try:
        if request.method == 'POST':
            received_date = request.form['received_date']
            order_ids = request.form.getlist('order_id[]')
            material_ids = request.form.getlist('raw_material_id[]')
            vendor_ids = request.form.getlist('vendor_id[]')
            batch_nos = request.form.getlist('batch_no[]')
            qtys = request.form.getlist('qty_received[]')

            rows_to_insert = []
            for oid, mid, vid, bno, qty in zip(order_ids, material_ids, vendor_ids, batch_nos, qtys):
                if not mid or not vid or not bno.strip() or not qty:
                    continue
                qty_f = float(qty)
                if qty_f <= 0:
                    continue
                rows_to_insert.append({
                    'order_id': int(oid) if oid else None,
                    'raw_material_id': int(mid),
                    'vendor_id': int(vid),
                    'batch_no': bno.strip(),
                    'qty_received': qty_f,
                })

            if not rows_to_insert:
                flash('Add at least one valid receipt line (material, vendor, batch no. and quantity).', 'error')
                return redirect(url_for('receive'))

            group_id = uuid.uuid4().hex
            with db.cursor() as cur:
                for row in rows_to_insert:
                    cur.execute("""INSERT INTO raw_material_receipts
                                    (receipt_group_id, order_id, raw_material_id, vendor_id, batch_no,
                                     qty_received, received_date, received_by)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (group_id, row['order_id'], row['raw_material_id'], row['vendor_id'],
                                 row['batch_no'], row['qty_received'], received_date, session['user_id']))
                    if row['order_id']:
                        cur.execute("""SELECT qty_ordered,
                                        (SELECT COALESCE(SUM(qty_received),0) FROM raw_material_receipts
                                         WHERE order_id=%s) AS total_received
                                        FROM raw_material_orders WHERE id=%s""",
                                    (row['order_id'], row['order_id']))
                        r2 = cur.fetchone()
                        new_status = 'received' if r2['total_received'] >= r2['qty_ordered'] else 'partially_received'
                        cur.execute("UPDATE raw_material_orders SET status=%s WHERE id=%s",
                                    (new_status, row['order_id']))
            db.commit()
            flash(f"{len(rows_to_insert)} item(s) received. Pending quality check before they count as "
                  f"usable stock.", 'success')
            return redirect(url_for('receive'))

        with db.cursor() as cur:
            cur.execute("SELECT * FROM raw_materials ORDER BY name")
            materials = cur.fetchall()
            cur.execute("SELECT * FROM vendors ORDER BY name")
            vendor_list = cur.fetchall()
            cur.execute("""SELECT o.id, o.order_group_id, o.raw_material_id, o.vendor_id, o.qty_ordered,
                                   o.status, o.order_date, rm.name AS material_name, rm.unit, v.name AS vendor_name
                            FROM raw_material_orders o
                            JOIN raw_materials rm ON rm.id = o.raw_material_id
                            JOIN vendors v ON v.id = o.vendor_id
                            WHERE o.status IN ('pending','partially_received')
                            ORDER BY o.order_date DESC""")
            open_order_rows = cur.fetchall()
            cur.execute("""SELECT r.*, rm.name AS material_name, rm.unit, v.name AS vendor_name
                            FROM raw_material_receipts r
                            JOIN raw_materials rm ON rm.id = r.raw_material_id
                            JOIN vendors v ON v.id = r.vendor_id
                            ORDER BY r.created_at DESC LIMIT 50""")
            receipts = cur.fetchall()
    finally:
        db.close()

    # Group pending order lines by order_group_id so the Receive page can offer
    # "load this whole order" instead of picking each material one at a time.
    # Legacy orders with no group id each become their own single-item group.
    groups_by_key = {}
    order_groups = []
    for o in open_order_rows:
        key = o['order_group_id'] or f"single-{o['id']}"
        if key not in groups_by_key:
            g = {
                'group_key': key,
                'vendor_name': o['vendor_name'],
                'order_date': o['order_date'],
                'lines': [],
            }
            groups_by_key[key] = g
            order_groups.append(g)
        groups_by_key[key]['lines'].append(o)

    open_orders = open_order_rows

    return render_template('receive.html', materials=materials, vendors=vendor_list,
                            open_orders=open_orders, order_groups=order_groups, receipts=receipts,
                            today=date.today().isoformat())


# ---------------------------------------------------------------------
# Quality Check
#   Every raw material receipt must pass a fixed checklist of mandatory
#   checks (see qc_criteria table / migration_qc_checklist.sql). Stock is
#   only added if EVERY active criteria is marked "pass". If even one
#   item is marked "fail", the whole receipt is marked failed and no
#   stock is added.
# ---------------------------------------------------------------------
@app.route('/qc', methods=['GET', 'POST'])
@role_required('admin', 'store')
def qc():
    db = get_db()
    try:
        if request.method == 'POST':
            receipt_id = int(request.form['receipt_id'])
            overall_remarks = request.form.get('remarks', '')

            with db.cursor() as cur:
                cur.execute("SELECT * FROM raw_material_receipts WHERE id=%s FOR UPDATE", (receipt_id,))
                receipt = cur.fetchone()
                if not receipt or receipt['qc_status'] != 'pending':
                    db.rollback()
                    flash('This batch has already been checked.', 'error')
                    return redirect(url_for('qc'))

                cur.execute("SELECT * FROM qc_criteria WHERE is_active=1 ORDER BY sort_order")
                criteria_list = cur.fetchall()
                if not criteria_list:
                    db.rollback()
                    flash('No QC checklist items are configured. Contact an admin.', 'error')
                    return redirect(url_for('qc'))

                # Every criteria must be explicitly marked pass/fail — this is
                # what makes the checklist mandatory rather than optional.
                check_rows = []
                all_passed = True
                for c in criteria_list:
                    result = request.form.get(f"criteria_{c['id']}")
                    if result not in ('pass', 'fail'):
                        db.rollback()
                        flash('Please mark every checklist item as Pass or Fail before submitting.', 'error')
                        return redirect(url_for('qc'))
                    if result == 'fail':
                        all_passed = False
                    check_rows.append((c['id'], result))

                overall_result = 'pass' if all_passed else 'fail'

                cur.execute("""INSERT INTO qc_checks (receipt_id, result, remarks, checked_by)
                                VALUES (%s,%s,%s,%s)""",
                            (receipt_id, overall_result, overall_remarks, session['user_id']))
                qc_check_id = cur.lastrowid

                for criteria_id, result in check_rows:
                    cur.execute("""INSERT INTO qc_check_items (qc_check_id, receipt_id, criteria_id, result)
                                    VALUES (%s,%s,%s,%s)""", (qc_check_id, receipt_id, criteria_id, result))

                cur.execute("UPDATE raw_material_receipts SET qc_status=%s WHERE id=%s",
                            ('passed' if overall_result == 'pass' else 'failed', receipt_id))

                if overall_result == 'pass':
                    cur.execute("UPDATE raw_materials SET current_stock = current_stock + %s WHERE id=%s",
                                (receipt['qty_received'], receipt['raw_material_id']))
                    cur.execute("SELECT current_stock FROM raw_materials WHERE id=%s", (receipt['raw_material_id'],))
                    new_balance = cur.fetchone()['current_stock']
                    cur.execute("""INSERT INTO stock_ledger
                                    (item_type, item_id, transaction_type, qty, reference_type, reference_id, balance_after, remarks, created_by)
                                    VALUES ('raw_material', %s, 'in', %s, 'qc_pass', %s, %s, %s, %s)""",
                                (receipt['raw_material_id'], receipt['qty_received'], receipt_id, new_balance,
                                 f"Batch {receipt['batch_no']} passed all {len(criteria_list)} QC checks",
                                 session['user_id']))
            db.commit()
            if overall_result == 'pass':
                flash('All checklist items passed \u2014 quality check recorded and stock added.', 'success')
            else:
                flash('One or more checklist items failed \u2014 batch marked as failed. Stock was NOT added.',
                      'error')
            return redirect(url_for('qc'))

        with db.cursor() as cur:
            cur.execute("SELECT * FROM qc_criteria WHERE is_active=1 ORDER BY sort_order")
            criteria_list = cur.fetchall()

            cur.execute("""SELECT r.*, rm.name AS material_name, rm.unit, v.name AS vendor_name
                            FROM raw_material_receipts r
                            JOIN raw_materials rm ON rm.id = r.raw_material_id
                            JOIN vendors v ON v.id = r.vendor_id
                            WHERE r.qc_status='pending'
                            ORDER BY r.created_at ASC""")
            pending = cur.fetchall()

            cur.execute("""SELECT qc.*, r.batch_no, rm.name AS material_name FROM qc_checks qc
                            JOIN raw_material_receipts r ON r.id = qc.receipt_id
                            JOIN raw_materials rm ON rm.id = r.raw_material_id
                            ORDER BY qc.checked_at DESC LIMIT 20""")
            recent_checks = cur.fetchall()
            for rc in recent_checks:
                cur.execute("""SELECT qcr.name, qci.result FROM qc_check_items qci
                                JOIN qc_criteria qcr ON qcr.id = qci.criteria_id
                                WHERE qci.qc_check_id=%s ORDER BY qcr.sort_order""", (rc['id'],))
                rc['checklist_items'] = cur.fetchall()
    finally:
        db.close()
    return render_template('qc.html', pending=pending, recent_checks=recent_checks, criteria_list=criteria_list)


# ---------------------------------------------------------------------
# Production (auto-deducts raw materials based on BOM, FIFO across batches)
# ---------------------------------------------------------------------
@app.route('/production', methods=['GET', 'POST'])
@role_required('admin', 'store')
def production():
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM finished_products ORDER BY name")
            products = cur.fetchall()

        if request.method == 'POST':
            fp_id = int(request.form['finished_product_id'])
            qty_produced = float(request.form['qty_produced'])
            batch_code = request.form['batch_code'].strip()
            production_date = request.form['production_date']
            notes = request.form.get('notes', '')

            with db.cursor() as cur:
                cur.execute("SELECT id FROM production_batches WHERE batch_code=%s", (batch_code,))
                if cur.fetchone():
                    flash(f'Batch code "{batch_code}" is already used. Enter a unique batch code.', 'error')
                    return redirect(url_for('production'))
                cur.execute("SELECT * FROM bom WHERE finished_product_id=%s", (fp_id,))
                bom_rows = cur.fetchall()
                if not bom_rows:
                    flash('No recipe (BOM) has been set up for this product yet.', 'error')
                    return redirect(url_for('production'))

            try:
                with db.cursor() as cur:
                    consumption_plan = []
                    for row in bom_rows:
                        rm_id = row['raw_material_id']
                        needed = float(row['qty_required']) * qty_produced
                        cur.execute("""
                            SELECT r.id, r.batch_no,
                                (r.qty_received - COALESCE((SELECT SUM(pc.qty_consumed) FROM production_consumption pc
                                                             WHERE pc.raw_material_receipt_id = r.id), 0)) AS available
                            FROM raw_material_receipts r
                            WHERE r.raw_material_id=%s AND r.qc_status='passed'
                            ORDER BY r.received_date ASC, r.id ASC
                        """, (rm_id,))
                        receipts = cur.fetchall()
                        remaining = needed
                        for r in receipts:
                            if remaining <= 0:
                                break
                            avail = float(r['available'])
                            if avail <= 0:
                                continue
                            take = min(avail, remaining)
                            consumption_plan.append((rm_id, r['id'], take))
                            remaining -= take
                        if remaining > 0.0001:
                            cur.execute("SELECT name, unit FROM raw_materials WHERE id=%s", (rm_id,))
                            m = cur.fetchone()
                            raise ValueError(f"Not enough {m['name']} in stock. Short by {remaining:.3f} {m['unit']}.")

                    cur.execute("""INSERT INTO production_batches
                                    (batch_code, finished_product_id, qty_produced, production_date, produced_by, notes)
                                    VALUES (%s,%s,%s,%s,%s,%s)""",
                                (batch_code, fp_id, qty_produced, production_date, session['user_id'], notes))
                    production_batch_id = cur.lastrowid

                    material_totals = {}
                    for rm_id, receipt_id, take in consumption_plan:
                        cur.execute("""INSERT INTO production_consumption
                                        (production_batch_id, raw_material_id, raw_material_receipt_id, qty_consumed)
                                        VALUES (%s,%s,%s,%s)""", (production_batch_id, rm_id, receipt_id, take))
                        material_totals[rm_id] = material_totals.get(rm_id, 0) + take

                    for rm_id, total_take in material_totals.items():
                        cur.execute("UPDATE raw_materials SET current_stock = current_stock - %s WHERE id=%s",
                                    (total_take, rm_id))
                        cur.execute("SELECT current_stock FROM raw_materials WHERE id=%s", (rm_id,))
                        bal = cur.fetchone()['current_stock']
                        cur.execute("""INSERT INTO stock_ledger
                                        (item_type, item_id, transaction_type, qty, reference_type, reference_id, balance_after, remarks, created_by)
                                        VALUES ('raw_material', %s, 'out', %s, 'production_consumption', %s, %s, %s, %s)""",
                                    (rm_id, total_take, production_batch_id, bal, f"Used in batch {batch_code}",
                                     session['user_id']))

                    cur.execute("UPDATE finished_products SET current_stock = current_stock + %s WHERE id=%s",
                                (qty_produced, fp_id))
                    cur.execute("SELECT current_stock FROM finished_products WHERE id=%s", (fp_id,))
                    fp_bal = cur.fetchone()['current_stock']
                    cur.execute("""INSERT INTO stock_ledger
                                    (item_type, item_id, transaction_type, qty, reference_type, reference_id, balance_after, remarks, created_by)
                                    VALUES ('finished_product', %s, 'in', %s, 'production_output', %s, %s, %s, %s)""",
                                (fp_id, qty_produced, production_batch_id, fp_bal, f"Batch {batch_code} produced",
                                 session['user_id']))
                db.commit()
                flash(f'Production batch "{batch_code}" recorded. Raw materials deducted automatically.', 'success')
            except ValueError as e:
                db.rollback()
                flash(str(e), 'error')
            return redirect(url_for('production'))

        with db.cursor() as cur:
            cur.execute("""SELECT pb.*, fp.name AS product_name, fp.unit FROM production_batches pb
                            JOIN finished_products fp ON fp.id = pb.finished_product_id
                            ORDER BY pb.created_at DESC LIMIT 30""")
            recent = cur.fetchall()
    finally:
        db.close()
    return render_template('production.html', products=products, recent=recent, today=date.today().isoformat())


@app.route('/production/<int:batch_id>/trace')
@role_required('admin', 'store')
def production_trace(batch_id):
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute("""SELECT pb.*, fp.name AS product_name, fp.unit FROM production_batches pb
                            JOIN finished_products fp ON fp.id = pb.finished_product_id WHERE pb.id=%s""", (batch_id,))
            batch = cur.fetchone()
            cur.execute("""SELECT pc.qty_consumed, rm.name AS material_name, rm.unit,
                                   r.batch_no, v.name AS vendor_name
                            FROM production_consumption pc
                            JOIN raw_materials rm ON rm.id = pc.raw_material_id
                            JOIN raw_material_receipts r ON r.id = pc.raw_material_receipt_id
                            JOIN vendors v ON v.id = r.vendor_id
                            WHERE pc.production_batch_id=%s""", (batch_id,))
            consumption = cur.fetchall()
    finally:
        db.close()
    return render_template('trace.html', batch=batch, consumption=consumption)


# ---------------------------------------------------------------------
# Stock ledger (full audit trail)
# ---------------------------------------------------------------------
@app.route('/stock-ledger')
@role_required('admin', 'store')
def stock_ledger():
    item_type = request.args.get('item_type', '')
    db = get_db()
    try:
        with db.cursor() as cur:
            if item_type in ('raw_material', 'finished_product'):
                cur.execute("SELECT * FROM stock_ledger WHERE item_type=%s ORDER BY created_at DESC LIMIT 200",
                            (item_type,))
            else:
                cur.execute("SELECT * FROM stock_ledger ORDER BY created_at DESC LIMIT 200")
            ledger = cur.fetchall()
            for row in ledger:
                table = 'raw_materials' if row['item_type'] == 'raw_material' else 'finished_products'
                cur.execute(f"SELECT name, unit FROM {table} WHERE id=%s", (row['item_id'],))
                item = cur.fetchone()
                row['item_name'] = item['name'] if item else 'Unknown'
                row['unit'] = item['unit'] if item else ''
    finally:
        db.close()
    return render_template('stock_ledger.html', ledger=ledger, item_type=item_type)


if __name__ == '__main__':
    app.run(debug=True)