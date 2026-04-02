"""
Quantitative Portfolio Analyzer — Extended Edition v2
Reads a Fidelity Positions CSV export AND a closed positions CSV,
then generates a full HTML report.

Closed positions additions:
  • parse_closed() reads /content/closed2026.csv with exact Fidelity format:
    Account Number, Account Name, Symbol, Description, Cost Basis,
    Proceeds, Short Term Gain/Loss, Long Term Gain/Loss, Total Term Gain/Loss
  • Realized P&L summary (total realized, win/loss rate on closed trades)
  • Realized vs Unrealized P&L comparison chart
  • Closed P&L by symbol chart
  • Closed positions detail table at bottom of report
  • Combined batting average (open + closed) shown alongside open-only

Usage: just run the cell. Change FILEPATH / CLOSED_FILEPATH below if needed.
"""

import csv
import json
import os
import re
from datetime import datetime, date

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION — edit these two lines
# ─────────────────────────────────────────────────────────────────────────────
FILEPATH        = '/content/Positions.csv'     # <- open positions CSV
CLOSED_FILEPATH = '/content/closed2026.csv'    # <- closed positions CSV
# ─────────────────────────────────────────────────────────────────────────────

TODAY = date.today()

PALETTE = ['#378ADD','#1D9E75','#639922','#5DCAA5','#BA7517',
           '#D85A30','#7F77DD','#EF9F27','#D4537E','#888780',
           '#E24B4A','#0F6E56','#185FA5','#3B6D11','#63C5E0']

# ── helpers ───────────────────────────────────────────────────────────────────

def clean(val):
    if not val or str(val).strip() in ('--', '', 'N/A'):
        return None
    v = str(val).strip().replace('$','').replace('%','').replace(',','').replace('+','')
    try:
        return float(v)
    except ValueError:
        return None

def parse_date(s):
    if not s or s.strip() in ('--',''):
        return None
    for fmt in ('%b-%d-%Y','%Y-%m-%d','%m/%d/%Y','%m-%d-%Y'):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None

def days_to_exp(exp_str):
    d = parse_date(exp_str)
    if d is None:
        return None
    return (d - TODAY).days

def fmt_currency(v, decimals=2):
    if v is None: return '--'
    sign = '-' if v < 0 else ''
    return f"{sign}${abs(v):,.{decimals}f}"

def fmt_pct(v):
    if v is None: return '--'
    sign = '+' if v >= 0 else ''
    return f"{sign}{v:.2f}%"

def color_class(v):
    if v is None: return ''
    return 'pos' if v >= 0 else 'neg'

def urg_class(days):
    if days is None: return ''
    if days <= 7:  return 'urg-red'
    if days <= 21: return 'urg-amber'
    return 'urg-green'

# ── CSV parsing — open positions ──────────────────────────────────────────────

def parse_positions(filepath):
    stocks, etfs, options, cash_rows = [], [], [], []
    with open(filepath, newline='', encoding='utf-8-sig') as f:
        lines = [l for l in f if not l.strip().startswith('"Date downloaded')]
    reader = csv.DictReader(lines)
    for row in reader:
        inv_type = row.get('Investment Type','').strip()
        symbol   = row.get('Symbol','').strip()
        desc     = row.get('Description','').strip()
        acct     = row.get('Account Name','').strip()

        price        = clean(row.get('Last Price'))
        current_val  = clean(row.get('Current value'))
        cost_total   = clean(row.get('Cost basis total'))
        avg_cost     = clean(row.get('Average cost basis'))
        today_gl     = clean(row.get("Today's gain/loss $"))
        today_gl_pct = clean(row.get("Today's gain/loss %"))
        total_gl     = clean(row.get('Total gain/loss $'))
        total_gl_pct = clean(row.get('Total gain/loss %'))
        qty          = clean(row.get('Quantity'))
        vol_30       = clean(row.get('30-day volatility'))
        vol_90       = clean(row.get('90-day volatility'))
        sector       = row.get('Sector','').strip()

        cp       = row.get('Call/put','').strip()
        exp_date = row.get('Expiration','').strip()
        iv_raw   = clean(row.get('Implied volatility'))
        delta    = clean(row.get('Delta'))
        theta    = clean(row.get('Theta'))
        gamma    = clean(row.get('Gamma'))
        vega     = clean(row.get('Vega'))

        base = dict(
            symbol=symbol, description=desc, account=acct,
            price=price, current_val=current_val, cost_total=cost_total,
            avg_cost=avg_cost, today_gl=today_gl, today_gl_pct=today_gl_pct,
            total_gl=total_gl, total_gl_pct=total_gl_pct, qty=qty,
            vol_30=vol_30, vol_90=vol_90, sector=sector,
            cp=cp, exp_date=exp_date, iv=iv_raw,
            delta=delta, theta=theta, gamma=gamma, vega=vega,
            dte=days_to_exp(exp_date)
        )

        if inv_type == 'Stocks':
            stocks.append(base)
        elif inv_type == 'ETFs':
            etfs.append(base)
        elif inv_type in ('Options','Warrants'):
            options.append(base)
        elif inv_type == 'Cash' or symbol in ('SPAXX**','SPAXX','CORE**','USD***'):
            cash_rows.append(base)

    return stocks, etfs, options, cash_rows

# ── CSV parsing — closed positions ───────────────────────────────────────────
#
#  Exact Fidelity closed2026.csv column layout:
#    Account Number  <- asset type ('options' / 'stocks') lives here
#    Account Name
#    Symbol          <- OCC-style, e.g. ' -GOSS260220P1.5' (leading space+dash = short)
#    Description     <- e.g. 'PUT (GOSS) GOSSAMER BIO INC COM FEB 20 26 $1.5 (100 SHS)'
#    Cost Basis      <- e.g. '$1002.38'
#    Proceeds        <- e.g. '$3432.62'
#    Short Term Gain/Loss   <- '--' when long-term
#    Long Term Gain/Loss    <- e.g. '+$2430.24'
#    Total Term Gain/Loss   <- e.g. '+$2430.24'  ← we use this
#
#  File ends with a quoted "Date downloaded..." line — stripped before parsing.

def parse_closed(filepath):
    if not filepath or not os.path.exists(filepath):
        print(f"  [closed] File not found: {filepath} — skipping.")
        return []

    closed = []
    with open(filepath, newline='', encoding='utf-8-sig') as f:
        lines = [l for l in f if not l.strip().startswith('"Date downloaded')]

    reader = csv.DictReader(lines)

    for row in reader:
        # Skip fully blank rows
        if not any(v.strip() for v in row.values()):
            continue

        # Column 'Account Number' holds asset type in this export style
        inv_type = row.get('Account Number', '').strip().lower()
        acct     = row.get('Account Name', '').strip()
        symbol   = row.get('Symbol', '').strip()
        desc     = row.get('Description', '').strip()

        # Skip accidental header rows
        if symbol.lower() in ('symbol', 'ticker', ''):
            continue

        cost     = clean(row.get('Cost Basis'))
        proceeds = clean(row.get('Proceeds'))

        # Use Total Term G/L; fall back to ST+LT; then derive from proceeds-cost
        gl_dollar = clean(row.get('Total Term Gain/Loss'))
        if gl_dollar is None:
            st = clean(row.get('Short Term Gain/Loss'))
            lt = clean(row.get('Long Term Gain/Loss'))
            if st is not None and lt is not None:
                gl_dollar = st + lt
            elif proceeds is not None and cost is not None:
                gl_dollar = proceeds - cost

        gl_pct = None
        if gl_dollar is not None and cost and cost != 0:
            gl_pct = gl_dollar / abs(cost) * 100

        # Determine if short (symbol starts with '-' after stripping space)
        is_short = symbol.lstrip(' ').startswith('-')

        # Extract underlying and call/put from description
        cp         = ''
        underlying = symbol.strip().lstrip('- ')
        desc_up    = desc.upper()
        if desc_up.startswith('PUT'):
            cp = 'Put'
        elif desc_up.startswith('CALL'):
            cp = 'Call'
        m = re.search(r'\(([A-Z0-9]+)\)', desc_up)
        if m:
            underlying = m.group(1)

        closed.append(dict(
            symbol=symbol,
            underlying=underlying,
            description=desc,
            inv_type=inv_type,
            account=acct,
            cp=cp,
            is_short=is_short,
            cost=cost,
            proceeds=proceeds,
            gl_dollar=gl_dollar,
            gl_pct=gl_pct,
        ))

    print(f"  [closed] Loaded {len(closed)} closed-position rows.")
    return closed

# ── quant analytics ───────────────────────────────────────────────────────────

def quant_metrics(stocks, etfs, options, cash_rows, closed):
    equities = stocks + etfs

    total_eq   = sum(r['current_val'] for r in equities if r['current_val'])
    total_cash = sum(r['current_val'] for r in cash_rows if r['current_val'])
    total_port = total_eq + total_cash
    today_total= sum(r['today_gl'] for r in equities + options if r['today_gl'])

    # Open positions win/loss
    winners_open = [r for r in equities if (r['total_gl'] or 0) > 0]
    losers_open  = [r for r in equities if (r['total_gl'] or 0) < 0]
    batting_avg_open = len(winners_open) / len(equities) * 100 if equities else 0
    avg_win_open  = sum(r['total_gl'] for r in winners_open) / len(winners_open) if winners_open else 0
    avg_loss_open = sum(r['total_gl'] for r in losers_open)  / len(losers_open)  if losers_open  else 0
    wlr_open = abs(avg_win_open / avg_loss_open) if avg_loss_open else None

    # Closed positions analytics
    closed_with_gl  = [r for r in closed if r['gl_dollar'] is not None]
    closed_winners  = [r for r in closed_with_gl if r['gl_dollar'] > 0]
    closed_losers   = [r for r in closed_with_gl if r['gl_dollar'] < 0]
    total_realized  = sum(r['gl_dollar'] for r in closed_with_gl)
    batting_closed  = len(closed_winners) / len(closed_with_gl) * 100 if closed_with_gl else 0
    avg_win_closed  = sum(r['gl_dollar'] for r in closed_winners) / len(closed_winners) if closed_winners else 0
    avg_loss_closed = sum(r['gl_dollar'] for r in closed_losers)  / len(closed_losers)  if closed_losers  else 0
    wlr_closed = abs(avg_win_closed / avg_loss_closed) if avg_loss_closed else None

    realized_options = sum(r['gl_dollar'] for r in closed_with_gl if r['inv_type'] == 'options')
    realized_stocks  = sum(r['gl_dollar'] for r in closed_with_gl if r['inv_type'] == 'stocks')

    # Combined (open unrealized + closed realized)
    total_unrealized = sum(r['total_gl'] for r in equities if r['total_gl'] is not None)
    all_wins   = winners_open + closed_winners
    all_losses = losers_open  + closed_losers
    batting_combined = len(all_wins) / (len(all_wins) + len(all_losses)) * 100 if (all_wins or all_losses) else 0

    # HHI
    vals = [r['current_val'] for r in equities if (r['current_val'] or 0) > 0]
    total_vals = sum(vals)
    hhi = sum((v / total_vals * 100)**2 for v in vals) if total_vals else 0

    # Sharpe proxy
    sharpe_rows = []
    for r in equities:
        if r['total_gl_pct'] is not None and r['vol_90'] and r['vol_90'] > 0:
            sharpe_rows.append((r['symbol'], round(r['total_gl_pct'] / r['vol_90'], 3)))
    sharpe_rows.sort(key=lambda x: x[1], reverse=True)

    # Greeks
    net_delta   = sum((r['delta'] or 0) * (r['qty'] or 1) * 100 for r in options)
    total_theta = sum((r['theta'] or 0) * (r['qty'] or 1) * 100 for r in options)
    total_gamma = sum((r['gamma'] or 0) * abs(r['qty'] or 1) * 100 for r in options)
    total_vega  = sum((r['vega']  or 0) * abs(r['qty'] or 1) * 100 for r in options)

    # Premium capture
    short_opts     = [r for r in options if (r['qty'] or 0) < 0]
    total_received = sum(abs(r['cost_total'] or 0) for r in short_opts)
    total_current  = sum(abs(r['current_val'] or 0) for r in short_opts)
    capture_rate   = (1 - total_current / total_received) * 100 if total_received else None

    # Cost basis distance
    cbd = []
    for r in equities:
        if r['price'] and r['avg_cost'] and r['avg_cost'] > 0:
            pct = (r['price'] - r['avg_cost']) / r['avg_cost'] * 100
            cbd.append((r['symbol'], round(pct, 2)))
    cbd.sort(key=lambda x: x[1], reverse=True)

    # Sector map
    sector_map = {}
    for r in equities:
        s = r['sector'] or 'Unknown'
        sector_map[s] = sector_map.get(s, 0) + (r['current_val'] or 0)
    sector_sorted = sorted(sector_map.items(), key=lambda x: x[1], reverse=True)

    exp_rows = sorted([r for r in options if r['dte'] is not None], key=lambda r: r['dte'])

    def best_worst(lst, key):
        f = [r for r in lst if r[key] is not None]
        return (max(f, key=lambda r: r[key]), min(f, key=lambda r: r[key])) if f else (None, None)

    best_day,  worst_day  = best_worst(equities, 'today_gl_pct')
    best_all,  worst_all  = best_worst(equities, 'total_gl_pct')

    return dict(
        total_port=total_port, total_eq=total_eq, total_cash=total_cash,
        today_total=today_total,
        batting_avg=batting_avg_open, win_loss_ratio=wlr_open,
        avg_win=avg_win_open, avg_loss=avg_loss_open,
        n_winners=len(winners_open), n_losers=len(losers_open),
        total_realized=total_realized,
        realized_options=realized_options, realized_stocks=realized_stocks,
        batting_closed=batting_closed, wlr_closed=wlr_closed,
        avg_win_closed=avg_win_closed, avg_loss_closed=avg_loss_closed,
        n_closed_winners=len(closed_winners), n_closed_losers=len(closed_losers),
        n_closed_total=len(closed_with_gl),
        batting_combined=batting_combined,
        total_unrealized=total_unrealized,
        hhi=hhi, sharpe_rows=sharpe_rows,
        net_delta=net_delta, total_theta=total_theta,
        total_gamma=total_gamma, total_vega=total_vega,
        capture_rate=capture_rate, cbd=cbd,
        sector_sorted=sector_sorted, exp_rows=exp_rows,
        best_day=best_day, worst_day=worst_day,
        best_all=best_all, worst_all=worst_all,
        options=options, equities=equities,
        closed=closed, closed_with_gl=closed_with_gl,
    )

# ── chart data ────────────────────────────────────────────────────────────────

def make_charts(stocks, etfs, options, qm):
    equities = stocks + etfs

    alloc = sorted([r for r in equities if (r['current_val'] or 0) > 0],
                   key=lambda r: r['current_val'], reverse=True)
    donut = dict(labels=[r['symbol'] for r in alloc],
                 data=[round(r['current_val'],2) for r in alloc],
                 colors=PALETTE[:len(alloc)])

    tr_rows = sorted([r for r in equities if r['total_gl_pct'] is not None],
                     key=lambda r: r['total_gl_pct'], reverse=True)
    tr = dict(labels=[r['symbol'] for r in tr_rows],
              data=[round(r['total_gl_pct'],2) for r in tr_rows],
              colors=['#1D9E75' if r['total_gl_pct'] >= 0 else '#D85A30' for r in tr_rows])

    op_rows = sorted([r for r in options if r['total_gl'] is not None],
                     key=lambda r: r['total_gl'], reverse=True)
    op = dict(labels=[r['symbol'] for r in op_rows],
              data=[round(r['total_gl'],2) for r in op_rows],
              colors=['#1D9E75' if r['total_gl'] >= 0 else '#D85A30' for r in op_rows])

    vol_rows = sorted([r for r in stocks if r['vol_30'] and r['vol_90']],
                      key=lambda r: r['vol_30'], reverse=True)
    vol = dict(labels=[r['symbol'] for r in vol_rows],
               vol30=[round(r['vol_30'],2) for r in vol_rows],
               vol90=[round(r['vol_90'],2) for r in vol_rows])

    gk_rows = sorted([r for r in options if r['delta'] is not None],
                     key=lambda r: abs(r['delta']), reverse=True)
    greeks = dict(
        labels=[r['symbol'] for r in gk_rows],
        delta =[round((r['delta'] or 0)*(r['qty'] or 1)*100, 2) for r in gk_rows],
        theta =[round((r['theta'] or 0)*(r['qty'] or 1)*100, 2) for r in gk_rows],
        gamma =[round((r['gamma'] or 0)*abs(r['qty'] or 1)*100, 4) for r in gk_rows],
        vega  =[round((r['vega']  or 0)*abs(r['qty'] or 1)*100, 2) for r in gk_rows],
    )

    cbd = dict(labels=[x[0] for x in qm['cbd']],
               data=[x[1] for x in qm['cbd']],
               colors=['#1D9E75' if x[1] >= 0 else '#D85A30' for x in qm['cbd']])

    sec = dict(labels=[s[0][:22] for s in qm['sector_sorted']],
               data=[round(s[1],2) for s in qm['sector_sorted']],
               colors=PALETTE[:len(qm['sector_sorted'])])

    sp = dict(labels=[x[0] for x in qm['sharpe_rows']],
              data=[x[1] for x in qm['sharpe_rows']],
              colors=['#378ADD' if x[1] >= 0 else '#D85A30' for x in qm['sharpe_rows']])

    # Realized vs Unrealized
    rvsu = dict(
        labels=['Unrealized (open)', 'Realized (closed)'],
        data=[round(qm['total_unrealized'], 2), round(qm['total_realized'], 2)],
        colors=['#1D9E75' if qm['total_unrealized'] >= 0 else '#D85A30',
                '#378ADD' if qm['total_realized']   >= 0 else '#D85A30'],
    )

    # Closed P&L aggregated by underlying
    closed_sym = {}
    for r in qm['closed_with_gl']:
        key = r['underlying']
        closed_sym[key] = closed_sym.get(key, 0) + r['gl_dollar']
    closed_sym_sorted = sorted(closed_sym.items(), key=lambda x: x[1], reverse=True)
    closed_chart = dict(
        labels=[x[0] for x in closed_sym_sorted],
        data=[round(x[1], 2) for x in closed_sym_sorted],
        colors=['#1D9E75' if x[1] >= 0 else '#D85A30' for x in closed_sym_sorted],
    )

    return dict(donut=donut, tr=tr, op=op, vol=vol,
                greeks=greeks, cbd=cbd, sec=sec, sp=sp,
                rvsu=rvsu, closed_chart=closed_chart)

# ── HTML fragment builders ────────────────────────────────────────────────────

def build_equity_rows(stocks, etfs):
    rows = ''
    for r in sorted(stocks + etfs, key=lambda x: (x['current_val'] or 0), reverse=True):
        pill = 'pill-pos' if (r['total_gl_pct'] or 0) >= 0 else 'pill-neg'
        rows += (
            f"<tr>"
            f"<td><strong>{r['symbol']}</strong></td>"
            f"<td style='color:#888;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{r['description'][:45]}</td>"
            f"<td style='color:#888'>{r['account']}</td>"
            f"<td>{r['qty'] or '--'}</td>"
            f"<td>{fmt_currency(r['avg_cost'])}</td>"
            f"<td>{fmt_currency(r['price'])}</td>"
            f"<td><strong>{fmt_currency(r['current_val'])}</strong></td>"
            f"<td class='{color_class(r['today_gl'])}'>{fmt_currency(r['today_gl'])}</td>"
            f"<td class='{color_class(r['today_gl_pct'])}'>{fmt_pct(r['today_gl_pct'])}</td>"
            f"<td class='{color_class(r['total_gl'])}'>{fmt_currency(r['total_gl'])}</td>"
            f"<td><span class='pill {pill}'>{fmt_pct(r['total_gl_pct'])}</span></td>"
            f"</tr>"
        )
    return rows

def build_option_rows(options):
    rows = ''
    for r in sorted(options, key=lambda x: (x['dte'] or 9999)):
        pill = 'pill-pos' if (r['total_gl'] or 0) >= 0 else 'pill-neg'
        dte  = r['dte']
        dte_str = f"{dte}d" if dte is not None else '--'
        uc   = urg_class(dte)
        iv_str = f"{r['iv']*100:.1f}%" if r['iv'] else '--'
        d_str  = f"{r['delta']:.3f}" if r['delta'] is not None else '--'
        t_str  = f"{r['theta']:.4f}" if r['theta'] is not None else '--'
        g_str  = f"{r['gamma']:.4f}" if r['gamma'] is not None else '--'
        ve_str = f"{r['vega']:.4f}"  if r['vega']  is not None else '--'
        rows += (
            f"<tr>"
            f"<td><strong>{r['symbol']}</strong></td>"
            f"<td>{r['cp'] or 'Warrant'}</td>"
            f"<td>{r['exp_date'] or '--'}</td>"
            f"<td><span class='pill {uc}'>{dte_str}</span></td>"
            f"<td>{d_str}</td>"
            f"<td>{t_str}</td>"
            f"<td>{g_str}</td>"
            f"<td>{ve_str}</td>"
            f"<td>{iv_str}</td>"
            f"<td>{fmt_currency(r['current_val'])}</td>"
            f"<td class='{color_class(r['today_gl'])}'>{fmt_currency(r['today_gl'])}</td>"
            f"<td><span class='pill {pill}'>{fmt_pct(r['total_gl_pct'])}</span></td>"
            f"</tr>"
        )
    return rows

def build_closed_rows(closed):
    if not closed:
        return '<tr><td colspan="8" style="color:#888;text-align:center;padding:1rem">No closed positions loaded.</td></tr>'
    rows = ''
    # Sort by realized G/L descending (biggest winners first)
    for r in sorted(closed, key=lambda x: (x['gl_dollar'] or 0), reverse=True):
        pill   = 'pill-pos' if (r['gl_dollar'] or 0) >= 0 else 'pill-neg'
        gl_pct = fmt_pct(r['gl_pct']) if r['gl_pct'] is not None else '--'
        cp_str = r['cp'] or ('Stock' if r['inv_type'] == 'stocks' else r['inv_type'].title())
        short_badge = (
            "<span class='pill pill-neg' style='font-size:10px;padding:1px 5px'>Short</span>"
            if r['is_short'] else
            "<span class='pill pill-pos' style='font-size:10px;padding:1px 5px'>Long</span>"
        )
        rows += (
            f"<tr>"
            f"<td><strong>{r['underlying']}</strong></td>"
            f"<td>{short_badge}&nbsp;{cp_str}</td>"
            f"<td style='color:#888;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{r['description'][:60]}</td>"
            f"<td style='color:#888'>{r['account']}</td>"
            f"<td>{fmt_currency(r['cost'])}</td>"
            f"<td>{fmt_currency(r['proceeds'])}</td>"
            f"<td class='{color_class(r['gl_dollar'])}'><strong>{fmt_currency(r['gl_dollar'])}</strong></td>"
            f"<td><span class='pill {pill}'>{gl_pct}</span></td>"
            f"</tr>"
        )
    return rows

def build_expiry_timeline(exp_rows):
    if not exp_rows:
        return '<p style="color:#888;font-size:13px">No options with expiration data.</p>'
    color_map = {'urg-red':'#D85A30','urg-amber':'#BA7517','urg-green':'#1D9E75','':'#888'}
    html = '<div style="display:flex;flex-direction:column;gap:10px">'
    for r in exp_rows:
        dte = r['dte']
        uc  = urg_class(dte)
        bar = max(2, min(100, 100 - (dte / 90 * 100))) if dte is not None else 50
        bc  = color_map.get(uc, '#888')
        gl  = fmt_currency(r['total_gl'])
        gc  = color_class(r['total_gl'])
        cp  = r['cp'] or 'W'
        html += (
            f"<div style='display:flex;align-items:center;gap:10px;font-size:13px'>"
            f"<span style='width:160px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500'>{r['symbol']}</span>"
            f"<span style='width:55px;color:#888;font-size:11px'>{cp}</span>"
            f"<span style='width:75px;color:#888'>{r['exp_date']}</span>"
            f"<span style='width:40px'><span class='pill {uc}' style='font-size:11px'>{dte}d</span></span>"
            f"<div style='flex:1;background:#f0efe8;border-radius:4px;height:8px'>"
            f"<div style='width:{bar:.0f}%;background:{bc};height:8px;border-radius:4px'></div></div>"
            f"<span style='width:75px;text-align:right' class='{gc}'>{gl}</span>"
            f"</div>"
        )
    html += '</div>'
    return html

def build_sharpe_table(sharpe_rows):
    rows = ''
    for sym, val in sharpe_rows:
        bar   = min(100, abs(val) * 20)
        color = '#1D9E75' if val >= 0 else '#D85A30'
        rows += (
            f"<tr>"
            f"<td><strong>{sym}</strong></td>"
            f"<td style='width:55%'>"
            f"<div style='background:#f0efe8;border-radius:3px;height:10px'>"
            f"<div style='width:{bar:.0f}%;background:{color};height:10px;border-radius:3px'></div></div></td>"
            f"<td style='text-align:right;font-weight:500;color:{color}'>{val:.2f}</td>"
            f"</tr>"
        )
    return rows

# ── main HTML template ────────────────────────────────────────────────────────

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio Analyzer</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f0;color:#1a1a18;font-size:15px;line-height:1.6}}
.page{{max-width:1140px;margin:0 auto;padding:2rem 1.5rem}}
h1{{font-size:24px;font-weight:500;margin-bottom:4px}}
.subtitle{{font-size:13px;color:#888;margin-bottom:2rem}}
h2{{font-size:12px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;color:#888;margin:2rem 0 .75rem;border-top:.5px solid #e0dfd8;padding-top:1.5rem}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:.75rem}}
.metric{{background:#fff;border:.5px solid #e0dfd8;border-radius:10px;padding:.75rem 1rem}}
.metric .lbl{{font-size:12px;color:#888;margin-bottom:3px}}
.metric .val{{font-size:20px;font-weight:500}}
.metric .sub{{font-size:11px;color:#aaa;margin-top:2px}}
.pos{{color:#1D9E75}}.neg{{color:#D85A30}}
.charts-row{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:1.25rem}}
.box{{background:#fff;border:.5px solid #e0dfd8;border-radius:12px;padding:1rem;margin-bottom:1.25rem}}
.box-title{{font-size:13px;font-weight:500;margin-bottom:.75rem}}
.legend{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:8px;font-size:12px;color:#888}}
.legend span{{display:flex;align-items:center;gap:4px}}
.ld{{width:10px;height:10px;border-radius:2px;display:inline-block}}
table{{width:100%;font-size:13px;border-collapse:collapse}}
th{{text-align:left;font-weight:500;font-size:12px;color:#888;padding:7px 8px;border-bottom:1px solid #eee;white-space:nowrap}}
td{{padding:6px 8px;border-bottom:.5px solid #f0efe8;vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
.tbl-wrap{{background:#fff;border:.5px solid #e0dfd8;border-radius:12px;overflow:hidden;margin-bottom:1.25rem}}
.pill{{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:500;white-space:nowrap}}
.pill-pos{{background:#E1F5EE;color:#0F6E56}}
.pill-neg{{background:#FAECE7;color:#993C1D}}
.urg-red{{background:#FAECE7;color:#993C1D}}
.urg-amber{{background:#faeeda;color:#854F0B}}
.urg-green{{background:#E1F5EE;color:#0F6E56}}
.alert{{background:#FAECE7;border:.5px solid #f0b0a0;border-radius:10px;padding:.75rem 1rem;margin-bottom:.75rem;font-size:13px;color:#993C1D}}
.alert strong{{font-weight:500}}
.hhi-bar{{height:12px;border-radius:4px;background:linear-gradient(to right,#1D9E75 0%,#BA7517 40%,#D85A30 100%);position:relative;margin:8px 0}}
.hhi-needle{{position:absolute;top:-4px;width:3px;height:20px;background:#1a1a18;border-radius:2px;transform:translateX(-50%)}}
.realized-banner{{background:#EAF3FF;border:.5px solid #b0ccf0;border-radius:10px;padding:.75rem 1rem;margin-bottom:.75rem;font-size:13px;color:#185FA5}}
@media(max-width:700px){{.metrics{{grid-template-columns:repeat(2,1fr)}}.charts-row{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="page">
<h1>Portfolio Analyzer</h1>
<p class="subtitle">Generated {date} &nbsp;·&nbsp; {filepath}</p>

{alert_banners}

<h2>Summary</h2>
<div class="metrics">
  <div class="metric"><div class="lbl">Total portfolio</div><div class="val">{total_port}</div></div>
  <div class="metric"><div class="lbl">Equity value</div><div class="val">{total_eq}</div></div>
  <div class="metric"><div class="lbl">Cash &amp; MM</div><div class="val">{total_cash}</div></div>
  <div class="metric"><div class="lbl">Today's P&amp;L</div><div class="val {today_cls}">{today_total}</div></div>
</div>
<div class="metrics">
  <div class="metric"><div class="lbl">Best today</div><div class="val pos">{best_day}</div></div>
  <div class="metric"><div class="lbl">Worst today</div><div class="val neg">{worst_day}</div></div>
  <div class="metric"><div class="lbl">Best all-time</div><div class="val pos">{best_all}</div></div>
  <div class="metric"><div class="lbl">Worst all-time</div><div class="val neg">{worst_all}</div></div>
</div>

<h2>Realized P&amp;L — Closed Positions</h2>
<div class="realized-banner">
  &#128196; Closed positions loaded: <strong>{n_closed_total}</strong> trades &nbsp;·&nbsp;
  Total realized: <strong class="{realized_cls}">{total_realized}</strong> &nbsp;·&nbsp;
  Options: <strong class="{realized_opt_cls}">{realized_options}</strong> &nbsp;·&nbsp;
  Stocks: <strong class="{realized_stk_cls}">{realized_stocks}</strong>
</div>
<div class="metrics">
  <div class="metric">
    <div class="lbl">Total realized P&amp;L</div>
    <div class="val {realized_cls}">{total_realized}</div>
    <div class="sub">{n_closed_winners}W / {n_closed_losers}L closed trades</div>
  </div>
  <div class="metric">
    <div class="lbl">Closed batting average</div>
    <div class="val">{batting_closed:.0f}%</div>
    <div class="sub">win rate on closed positions</div>
  </div>
  <div class="metric">
    <div class="lbl">Closed win/loss ratio</div>
    <div class="val">{wlr_closed_str}</div>
    <div class="sub">avg win {avg_win_closed} · avg loss {avg_loss_closed}</div>
  </div>
  <div class="metric">
    <div class="lbl">Combined batting avg</div>
    <div class="val">{batting_combined:.0f}%</div>
    <div class="sub">open + closed positions</div>
  </div>
</div>
<div class="charts-row">
  <div class="box">
    <div class="box-title">Realized vs Unrealized P&amp;L ($)</div>
    <div style="position:relative;height:160px"><canvas id="rvsu"></canvas></div>
  </div>
  <div class="box">
    <div class="box-title">Realized P&amp;L by underlying ($)</div>
    <div style="position:relative;height:160px"><canvas id="closed-chart"></canvas></div>
  </div>
</div>

<h2>Open Positions — Quant Metrics</h2>
<div class="metrics">
  <div class="metric">
    <div class="lbl">Batting average (open)</div>
    <div class="val">{batting_avg:.0f}%</div>
    <div class="sub">{n_winners}W / {n_losers}L positions</div>
  </div>
  <div class="metric">
    <div class="lbl">Win / loss ratio (open)</div>
    <div class="val">{win_loss_ratio}</div>
    <div class="sub">avg win {avg_win} · avg loss {avg_loss}</div>
  </div>
  <div class="metric">
    <div class="lbl">Net portfolio delta</div>
    <div class="val {delta_cls}">{net_delta:.1f}</div>
    <div class="sub">share-equivalent directional exposure</div>
  </div>
  <div class="metric">
    <div class="lbl">Daily theta burn</div>
    <div class="val neg">{total_theta:.2f}</div>
    <div class="sub">$ per day, all options combined</div>
  </div>
</div>
<div class="metrics">
  <div class="metric">
    <div class="lbl">Portfolio vega</div>
    <div class="val">{total_vega:.2f}</div>
    <div class="sub">$ gain per 1% IV rise</div>
  </div>
  <div class="metric">
    <div class="lbl">Portfolio gamma</div>
    <div class="val">{total_gamma:.2f}</div>
    <div class="sub">delta shift per $1 underlying move</div>
  </div>
  <div class="metric">
    <div class="lbl">Short premium capture</div>
    <div class="val {capture_cls}">{capture_rate}</div>
    <div class="sub">of original short premium retained</div>
  </div>
  <div class="metric">
    <div class="lbl">HHI concentration</div>
    <div class="val">{hhi:.0f}</div>
    <div class="sub">{hhi_label}</div>
  </div>
</div>

<div class="box">
  <div class="box-title">Concentration risk — Herfindahl-Hirschman Index</div>
  <div style="font-size:12px;color:#888;margin-bottom:4px">0 = perfectly spread &nbsp;·&nbsp; 2,500 = moderate &nbsp;·&nbsp; 10,000 = single position &nbsp;·&nbsp; yours: <strong style="color:#1a1a18">{hhi:.0f}</strong></div>
  <div class="hhi-bar"><div class="hhi-needle" style="left:{hhi_needle_pct:.1f}%"></div></div>
  <div style="display:flex;justify-content:space-between;font-size:11px;color:#aaa;margin-top:2px"><span>0</span><span>2,500</span><span>5,000</span><span>10,000</span></div>
</div>

<h2>Expiration Timeline</h2>
<div class="box">
  <div style="display:flex;gap:16px;font-size:12px;margin-bottom:12px">
    <span><span class="pill urg-red">≤7d</span> urgent</span>
    <span><span class="pill urg-amber">≤21d</span> watch</span>
    <span><span class="pill urg-green">&gt;21d</span> ok</span>
    <span style="color:#888;margin-left:auto">sorted by DTE · right column = total P&amp;L</span>
  </div>
  {expiry_timeline}
</div>

<h2>Allocation</h2>
<div class="legend" id="donut-legend"></div>
<div class="box">
  <div class="box-title">Holdings by current value ($)</div>
  <div style="position:relative;height:260px;max-width:500px;margin:auto"><canvas id="donut"></canvas></div>
</div>
<div class="charts-row">
  <div class="box">
    <div class="box-title">Sector exposure ($)</div>
    <div style="position:relative;height:230px"><canvas id="sec"></canvas></div>
  </div>
  <div class="box">
    <div class="box-title">Cost basis distance from breakeven (%)</div>
    <div style="position:relative;height:230px"><canvas id="cbd"></canvas></div>
  </div>
</div>

<h2>Returns &amp; Volatility</h2>
<div class="charts-row">
  <div class="box">
    <div class="box-title">Total return % by position</div>
    <div style="position:relative;height:{tr_height}px"><canvas id="bar1"></canvas></div>
  </div>
  <div class="box">
    <div class="box-title">Sharpe proxy — return ÷ 90-day vol</div>
    <div style="font-size:12px;color:#888;margin-bottom:8px">higher = better risk-adjusted return</div>
    <table>{sharpe_table}</table>
  </div>
</div>
<div class="box">
  <div class="box-title">30-day vs 90-day annualised volatility (%)</div>
  <div class="legend">
    <span><span class="ld" style="background:#378ADD"></span>30-day</span>
    <span><span class="ld" style="background:#7F77DD"></span>90-day</span>
  </div>
  <div style="position:relative;height:220px"><canvas id="vol"></canvas></div>
</div>

<h2>Options — Greeks Dashboard</h2>
<div class="charts-row">
  <div class="box">
    <div class="box-title">Net delta per position (× 100 shares)</div>
    <div style="position:relative;height:{gk_height}px"><canvas id="gk-delta"></canvas></div>
  </div>
  <div class="box">
    <div class="box-title">Net theta — daily $ decay per position</div>
    <div style="position:relative;height:{gk_height}px"><canvas id="gk-theta"></canvas></div>
  </div>
</div>
<div class="charts-row">
  <div class="box">
    <div class="box-title">Options total P&amp;L ($)</div>
    <div style="position:relative;height:{op_height}px"><canvas id="bar2"></canvas></div>
  </div>
  <div class="box">
    <div class="box-title">Net vega — $ per 1% IV move per position</div>
    <div style="position:relative;height:{gk_height}px"><canvas id="gk-vega"></canvas></div>
  </div>
</div>

<h2>Holdings Detail</h2>
<div class="tbl-wrap">
  <table>
    <thead><tr>
      <th>Symbol</th><th>Description</th><th>Account</th>
      <th>Qty</th><th>Avg cost</th><th>Price</th><th>Value</th>
      <th>Today $</th><th>Today %</th><th>Total P&amp;L $</th><th>Total %</th>
    </tr></thead>
    <tbody>{equity_rows}</tbody>
  </table>
</div>

<h2>Options &amp; Warrants — Full Greeks</h2>
<div class="tbl-wrap">
  <table>
    <thead><tr>
      <th>Symbol</th><th>Type</th><th>Expiration</th><th>DTE</th>
      <th>Delta</th><th>Theta</th><th>Gamma</th><th>Vega</th>
      <th>IV</th><th>Value</th><th>Today $</th><th>Total %</th>
    </tr></thead>
    <tbody>{option_rows}</tbody>
  </table>
</div>

<h2>Closed Positions — 2026 Realized P&amp;L</h2>
<div class="tbl-wrap">
  <table>
    <thead><tr>
      <th>Underlying</th><th>Type</th><th>Description</th><th>Account</th>
      <th>Cost Basis</th><th>Proceeds</th><th>Realized G/L $</th><th>G/L %</th>
    </tr></thead>
    <tbody>{closed_rows}</tbody>
  </table>
</div>

</div>
<script>
const C = {charts_json};

function hbar(id, labels, data, colors, fmtFn) {{
  new Chart(document.getElementById(id), {{
    type: 'bar',
    data: {{ labels, datasets: [{{ data, backgroundColor: colors, borderRadius: 3 }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false, indexAxis: 'y',
      plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => fmtFn(ctx.parsed.x) }} }} }},
      scales: {{
        x: {{ ticks: {{ callback: v => fmtFn(v), font: {{ size: 11 }}, color: '#888' }}, grid: {{ color: 'rgba(0,0,0,.06)' }} }},
        y: {{ ticks: {{ font: {{ size: 11 }}, color: '#888' }} }}
      }}
    }}
  }});
}}

// donut
const dl = document.getElementById('donut-legend');
C.donut.labels.forEach((l,i) => {{ dl.innerHTML += `<span><span class="ld" style="background:${{C.donut.colors[i]}}"></span>${{l}}</span>`; }});
new Chart(document.getElementById('donut'), {{
  type: 'doughnut',
  data: {{ labels: C.donut.labels, datasets: [{{ data: C.donut.data, backgroundColor: C.donut.colors, borderWidth: 2, borderColor: '#f5f5f0' }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => ' $' + ctx.parsed.toLocaleString() }} }} }},
    cutout: '62%' }}
}});

hbar('bar1', C.tr.labels, C.tr.data, C.tr.colors, v => v.toFixed(1) + '%');
hbar('bar2', C.op.labels, C.op.data, C.op.colors, v => '$' + v.toFixed(2));
hbar('cbd',  C.cbd.labels, C.cbd.data, C.cbd.colors, v => v.toFixed(1) + '%');

// vol
new Chart(document.getElementById('vol'), {{
  type: 'bar',
  data: {{ labels: C.vol.labels, datasets: [
    {{ label: '30d', data: C.vol.vol30, backgroundColor: '#378ADD', borderRadius: 3 }},
    {{ label: '90d', data: C.vol.vol90, backgroundColor: '#7F77DD', borderRadius: 3 }}
  ] }},
  options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 11 }}, color: '#888', autoSkip: false }}, grid: {{ color: 'rgba(0,0,0,.06)' }} }},
      y: {{ ticks: {{ callback: v => v + '%', font: {{ size: 11 }}, color: '#888' }}, grid: {{ color: 'rgba(0,0,0,.06)' }} }}
    }} }}
}});

// sector
new Chart(document.getElementById('sec'), {{
  type: 'doughnut',
  data: {{ labels: C.sec.labels, datasets: [{{ data: C.sec.data, backgroundColor: C.sec.colors, borderWidth: 2, borderColor: '#f5f5f0' }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'right', labels: {{ font: {{ size: 11 }}, boxWidth: 12, color: '#888' }} }},
      tooltip: {{ callbacks: {{ label: ctx => ' $' + ctx.parsed.toLocaleString() }} }} }},
    cutout: '55%' }}
}});

// greeks
hbar('gk-delta', C.greeks.labels, C.greeks.delta, C.greeks.delta.map(v => v>=0 ? '#378ADD' : '#D85A30'), v => v.toFixed(1));
hbar('gk-theta', C.greeks.labels, C.greeks.theta, C.greeks.theta.map(v => v>=0 ? '#1D9E75' : '#D85A30'), v => '$' + v.toFixed(2));
hbar('gk-vega',  C.greeks.labels, C.greeks.vega,  C.greeks.vega.map(() => '#7F77DD'), v => '$' + v.toFixed(2));

// realized vs unrealized (vertical bar)
new Chart(document.getElementById('rvsu'), {{
  type: 'bar',
  data: {{ labels: C.rvsu.labels, datasets: [{{ data: C.rvsu.data, backgroundColor: C.rvsu.colors, borderRadius: 6 }}] }},
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: ctx => '$' + ctx.parsed.y.toLocaleString(undefined,{{minimumFractionDigits:2}}) }} }} }},
    scales: {{
      x: {{ ticks: {{ font: {{ size: 11 }}, color: '#888' }} }},
      y: {{ ticks: {{ callback: v => '$' + v.toLocaleString(), font: {{ size: 11 }}, color: '#888' }}, grid: {{ color: 'rgba(0,0,0,.06)' }} }}
    }} }}
}});

// closed P&L by underlying (horizontal bar)
hbar('closed-chart', C.closed_chart.labels, C.closed_chart.data, C.closed_chart.colors, v => '$' + v.toFixed(2));
</script>
</body>
</html>"""

# ── assemble & write ──────────────────────────────────────────────────────────

def safe_label(r, key):
    return f"{r['symbol']} {fmt_pct(r[key])}" if r else '--'

def main():
    if not os.path.exists(FILEPATH):
        print(f"[ERROR] File not found: {FILEPATH}")
        return

    print(f"Reading {FILEPATH} ...")
    stocks, etfs, options, cash_rows = parse_positions(FILEPATH)
    print(f"  Stocks:{len(stocks)}  ETFs:{len(etfs)}  Options:{len(options)}  Cash:{len(cash_rows)}")

    print(f"Reading {CLOSED_FILEPATH} ...")
    closed = parse_closed(CLOSED_FILEPATH)

    qm = quant_metrics(stocks, etfs, options, cash_rows, closed)
    ch = make_charts(stocks, etfs, options, qm)

    # Red alert banners for options expiring <=7 days
    alerts = ''
    for r in qm['exp_rows']:
        if r['dte'] is not None and r['dte'] <= 7:
            alerts += (f"<div class='alert'>&#9888; <strong>{r['symbol']}</strong> expires in "
                       f"<strong>{r['dte']} days</strong> ({r['exp_date']}) — "
                       f"value {fmt_currency(r['current_val'])} · total P&L {fmt_pct(r['total_gl_pct'])}</div>")

    hhi = qm['hhi']
    hhi_label = ('well diversified' if hhi < 1000 else
                 'moderate concentration' if hhi < 2500 else 'highly concentrated')
    wlr  = qm['win_loss_ratio']
    cr   = qm['capture_rate']
    wlrc = qm['wlr_closed']

    html = TEMPLATE.format(
        date=datetime.now().strftime('%b %d, %Y %I:%M %p'),
        filepath=os.path.basename(FILEPATH),
        alert_banners=alerts,
        total_port=fmt_currency(qm['total_port']),
        total_eq=fmt_currency(qm['total_eq']),
        total_cash=fmt_currency(qm['total_cash']),
        today_total=fmt_currency(qm['today_total']),
        today_cls=color_class(qm['today_total']),
        best_day=safe_label(qm['best_day'],  'today_gl_pct'),
        worst_day=safe_label(qm['worst_day'], 'today_gl_pct'),
        best_all=safe_label(qm['best_all'],   'total_gl_pct'),
        worst_all=safe_label(qm['worst_all'], 'total_gl_pct'),
        n_closed_total=qm['n_closed_total'],
        total_realized=fmt_currency(qm['total_realized']),
        realized_cls=color_class(qm['total_realized']),
        realized_options=fmt_currency(qm['realized_options']),
        realized_opt_cls=color_class(qm['realized_options']),
        realized_stocks=fmt_currency(qm['realized_stocks']),
        realized_stk_cls=color_class(qm['realized_stocks']),
        n_closed_winners=qm['n_closed_winners'],
        n_closed_losers=qm['n_closed_losers'],
        batting_closed=qm['batting_closed'],
        wlr_closed_str=f"{wlrc:.2f}x" if wlrc else '--',
        avg_win_closed=fmt_currency(qm['avg_win_closed'], 0),
        avg_loss_closed=fmt_currency(qm['avg_loss_closed'], 0),
        batting_combined=qm['batting_combined'],
        batting_avg=qm['batting_avg'],
        n_winners=qm['n_winners'], n_losers=qm['n_losers'],
        win_loss_ratio=f"{wlr:.2f}x" if wlr else '--',
        avg_win=fmt_currency(qm['avg_win'], 0),
        avg_loss=fmt_currency(qm['avg_loss'], 0),
        net_delta=qm['net_delta'],
        delta_cls=color_class(qm['net_delta']),
        total_theta=qm['total_theta'],
        total_vega=qm['total_vega'],
        total_gamma=qm['total_gamma'],
        capture_rate=fmt_pct(cr) if cr is not None else '--',
        capture_cls='pos' if (cr or 0) > 0 else 'neg',
        hhi=hhi, hhi_label=hhi_label,
        hhi_needle_pct=min(98, hhi / 100),
        expiry_timeline=build_expiry_timeline(qm['exp_rows']),
        equity_rows=build_equity_rows(stocks, etfs),
        option_rows=build_option_rows(options),
        closed_rows=build_closed_rows(closed),
        sharpe_table=build_sharpe_table(qm['sharpe_rows']),
        charts_json=json.dumps(ch),
        tr_height=max(200, len(stocks+etfs)*36+60),
        op_height=max(180, len(options)*36+60),
        gk_height=max(180, len(options)*36+60),
    )

    out_path = os.path.join(os.path.dirname(FILEPATH) or '.', 'portfolio_report.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n  Report saved -> {out_path}")
    print(f"   HHI = {hhi:.0f} ({hhi_label})")
    print(f"   Open   — Batting: {qm['batting_avg']:.0f}%  W/L: {f'{wlr:.2f}x' if wlr else '--'}")
    print(f"   Closed — Batting: {qm['batting_closed']:.0f}%  W/L: {f'{wlrc:.2f}x' if wlrc else '--'}  Realized: ${qm['total_realized']:,.2f}")
    print(f"   Combined batting avg: {qm['batting_combined']:.0f}%")
    print(f"   Net delta = {qm['net_delta']:.1f}  |  Daily theta = ${qm['total_theta']:.2f}")
    urgent = [r for r in qm['exp_rows'] if r['dte'] is not None and r['dte'] <= 7]
    if urgent:
        print(f"\n   URGENT expirations:")
        for r in urgent:
            print(f"      {r['symbol']} -- {r['dte']} days  ({r['exp_date']})")

if __name__ == '__main__':
    main()