"""Self-contained comparison view, extending the existing evidence report's Read mode."""
from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import quote, urlsplit

from .report_html import _STYLE, _public_text


def _text(value: Any) -> str:
    return html.escape(_public_text(value), quote=True)


def _public_fields(value: Any) -> Any:
    if isinstance(value, dict):
        private = {"base_url", "student_url", "endpoint", "endpoint_url", "machine", "machine_id",
                   "snapshot", "snapshot_id", "golden_snapshot"}
        return {key: "[infrastructure value omitted]" if key in private else _public_fields(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_public_fields(item) for item in value]
    return value


def _dump(value: Any) -> str:
    return '<pre>' + _text(json.dumps(_public_fields(value), indent=2, ensure_ascii=False)) + '</pre>'


def _report_link(path: Any) -> str:
    # summarize_comparison has already checked existence and containment. Reject
    # URL/path syntax again so rendering a supplied summary cannot emit active URLs.
    if not isinstance(path, str) or not path.startswith('runs/') or '\\' in path:
        return ''
    url = urlsplit(path)
    if url.scheme or url.netloc or url.query or url.fragment or any(p in ('..', '.') for p in path.split('/')):
        return ''
    return '<p><a href="' + _text(quote(path, safe='/')) + '">Open retained episode report</a> (requires the comparison directory)</p>'


def html_comparison(summary: dict[str, Any]) -> str:
    protocol = summary['protocol']
    parts = ['<header><h1>Matched policy comparison</h1><p>' + _text(protocol.get('world', 'Unknown world')) + ' / ' +
             _text(protocol.get('family', 'Unknown family')) + ' / ' + _text(protocol.get('split', 'Unknown split')) + '</p><p><strong>' +
             _text(protocol.get('evidence_kind', 'Unknown evidence kind')) + '</strong> · ' + _text(summary['execution']['status']) + '</p><p>' +
             _text(summary['recommendation']['message']) + '</p>' +
             ('<p>' + _text(summary['execution']['explanation']) + '</p>' if summary['execution'].get('explanation') else '') +
             '<nav aria-label="Report sections">'
             '<a href="#outcomes">Outcomes</a><a href="#changes">What changed</a><a href="#pairs">Matched seeds</a><a href="#evidence">Cell evidence</a>'
             '<a href="#protocol">Protocol and limits</a></nav></header>']
    parts.append('<section id="outcomes"><h2>Exact denominators</h2><p>' +
                 _text(f"{summary['matched_pairs']} of {summary['planned_pairs']} planned pairs are comparable.") +
                 ' Missing, setup and execution errors are unscored, never silently counted as task failures.</p>')
    parts.append('<div class="table-scroll" tabindex="0" role="region" aria-label="Per-arm outcome counts"><table><thead><tr>'
                 '<th scope="col">Policy arm</th><th scope="col">Pass / scored</th><th scope="col">Failed</th>'
                 '<th scope="col">Unscored</th><th scope="col">Planned</th></tr></thead><tbody>')
    for arm, result in summary['arms'].items():
        parts.append('<tr><th scope="row">' + _text(arm + ' / ' + result['label']) + '</th><td>' +
                     _text(f"{result['successes']} / {result['scored']}") + '</td><td>' + _text(result['failures']) +
                     '</td><td>' + _text(result['unscored']) + '</td><td>' + _text(result['planned']) + '</td></tr>')
    parts.append('</tbody></table></div><h3>Paired outcomes</h3><dl class="meta">')
    names = {'both_pass': 'Both pass', 'A_only': 'A only · B regression', 'B_only': 'B only · B improvement', 'neither': 'Neither passes'}
    for key, count in summary['paired_outcomes'].items():
        parts.append('<dt>' + names[key] + '</dt><dd>' + _text(count) + '</dd>')
    test = summary.get('paired_test')
    test_text = (f" · exact McNemar p = {test['p_value']:.3g} on {test['discordant']} discordant pairs"
                 if isinstance(test, dict) and 'p_value' in test else '')
    parts.append('</dl><p>Discordant seeds: ' + _text(summary['discordant_seeds']) + _text(test_text) + '</p></section>')
    parts.append('<section id="changes"><h2>What changed</h2>')
    changes = summary.get('configuration_changes')
    if changes:
        parts.append('<div class="table-scroll" tabindex="0" role="region" aria-label="Declared policy changes"><table>'
                     '<thead><tr><th scope="col">Setting</th><th scope="col">A</th><th scope="col">B</th></tr></thead><tbody>')
        for change in changes:
            endpoint = change['path'].rsplit('.', 1)[-1] in {'base_url', 'student_url', 'endpoint', 'endpoint_url'}
            a_value = '[endpoint omitted]' if endpoint else json.dumps(change['A'], ensure_ascii=False)
            b_value = '[endpoint omitted]' if endpoint else json.dumps(change['B'], ensure_ascii=False)
            parts.append('<tr><th scope="row"><code>' + _text(change['path']) + '</code></th><td>' +
                         _text(a_value) + '</td><td>' + _text(b_value) + '</td></tr>')
        parts.append('</tbody></table></div>')
    else:
        parts.append('<p>No declared configuration differences are available. Inspect the saved identities below.</p>')
    parts.append('<p>These are declared settings, not proof of the code or weights served by a remote endpoint.</p>'
                 '<details><summary>Recorded work and token usage</summary>'
                 '<p>Cell timing includes setup and episode cleanup. Token counts include only reported usage; '
                 'unreported or uncertain billing is not zero. These are not invoices.</p>')
    for arm, result in summary['arms'].items():
        parts.append('<h3>' + _text(arm + ' / ' + result['label']) + '</h3>' +
                     _dump({key: result.get(key) for key in ('recorded_steps', 'recorded_setup_and_episode_seconds',
                                                            'duration_cells', 'recorded_tokens', 'usage_cells')}))
    parts.append('</details></section>')
    parts.append('<section id="pairs"><h2>Matched seeds</h2>')
    for pair in summary['pairs']:
        parts.append('<article class="check"><h3>Seed ' + _text(pair['seed']) + ' · ' +
                     _text(names.get(pair['outcome'], 'Not comparable')) + '</h3>')
        if pair['reasons']:
            parts.append('<ul>' + ''.join('<li>' + _text(reason) + '</li>' for reason in pair['reasons']) + '</ul>')
        parts.append('<p>' + ' · '.join('<a href="#cell-' + _text(cell_id) + '">' + _text(arm) + ' evidence</a>'
                                       for arm, cell_id in pair['cells'].items()) + '</p><details><summary>Reset equivalence</summary>' +
                     _dump(pair['reset_equivalence']) + '</details></article>')
    parts.append('</section><section id="evidence"><h2>Every planned cell</h2><p>Check details are embedded below. '
                 'Episode paths locate the normal manifests, step records and screenshots; external episode links appear only for existing reports.</p>')
    for cell in summary['cells']:
        parts.append('<article class="check" id="cell-' + _text(cell['id']) + '"><h3>' + _text(cell['arm']) + ' / seed ' +
                     _text(cell['seed']) + ' · ' + _text(cell['status']) + '</h3><p>Episode: <code>' +
                     _text(cell.get('episode') or 'No episode allocated') + '</code></p>')
        outcome = 'Passed' if cell['success'] is True else 'Failed' if cell['scored'] else 'Unscored'
        parts.append('<p><strong>' + outcome + '</strong>' +
                     (' · saved attempt status: ' + _text(cell['recorded_status']) if cell.get('recorded_status') else '') + '</p>')
        for key in ('error', 'cleanup_error', 'policy_cleanup_error', 'report_error'):
            if cell.get(key):
                parts.append('<p><strong>' + _text(key.replace('_', ' ')) + ':</strong> ' + _text(cell[key]) + '</p>')
        if cell['issues']:
            parts.append('<ul>' + ''.join('<li>' + _text(issue) + '</li>' for issue in cell['issues']) + '</ul>')
        evidence = cell.get('evidence')
        if evidence:
            verdict = evidence.get('verdict') or {}
            parts.append('<p>Retained verifier result: <strong>' + _text(verdict.get('reason_code', 'No verdict')) + '</strong> · ' +
                         _text(f"{evidence['steps']} recorded steps; wall seconds: {evidence['wall_seconds']}") + '</p>')
            details = verdict.get('details') or {}
            for check_id, detail in details.items():
                parts.append('<details><summary>Check: ' + _text(check_id) + '</summary>' + _dump(detail) + '</details>')
            parts.append('<details><summary>Complete verdict and recorded usage</summary>' + _dump(evidence) + '</details>')
        else:
            parts.append('<p>Unscored. No readable episode evidence is available for this cell.</p>')
        parts.append(_report_link(cell.get('report')))
        parts.append('<details><summary>Attempt and timing metadata</summary>' +
                     _dump({k: v for k, v in cell.items() if k not in ('evidence', 'baseline_digest')}) + '</details></article>')
    parts.append('</section><section id="protocol"><h2>Protocol, integrity and limits</h2><ul>' +
                 ''.join('<li>' + _text(limit) + '</li>' for limit in summary['limits']) + '</ul>')
    if summary['issues']:
        parts.append('<h3>Integrity issues</h3><ul>' + ''.join('<li>' + _text(issue) + '</li>' for issue in summary['issues']) + '</ul>')
    parts.append('<details><summary>Saved policy identities and protocol</summary>' + _dump(protocol) + '</details>'
                 '<details><summary>Execution and infrastructure events</summary>' + _dump(summary['execution']) + '</details>')
    if summary['unplanned_attempts']:
        parts.append('<details open><summary>Unplanned attempts retained</summary>' + _dump(summary['unplanned_attempts']) + '</details>')
    parts.append('</section><footer><p>Offline inspection, not a fresh execution. No scripts, fonts, analytics or network '
                 'dependencies. Check summaries are self-contained; linked episode reports require their original directory. '
                 'Review synthetic screenshots and model text before sharing.</p></footer>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            '<meta name="referrer" content="no-referrer"><title>Forkloop · Policy comparison</title><style>' + _STYLE +
            '\n.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:12px;border-bottom:1px solid #cbd2da}th{font-weight:650}article{scroll-margin-top:20px}</style></head>'
            '<body><a class="skip-link" href="#outcomes">Skip to outcomes</a><main>' + ''.join(parts) + '</main></body></html>')
