"""Project only the phase containing this pin, with separate pace and shape."""

from daengs_walk.diary.board.action_context import SHAPE_TEXT, context_uses


def current_motion(request):
    uses = context_uses(request)
    result = {'current_gait': [], 'current_shape': []}
    for use in uses:
        code = use['meaning']
        if code in {'relative_slow', 'relative_fast'}:
            key = 'current_gait'
            meaning = '이번 산책 기준보다 느린 걸음' if code == 'relative_slow' else '이번 산책 기준보다 빠른 걸음'
        elif code in SHAPE_TEXT:
            key, meaning = 'current_shape', SHAPE_TEXT[code]
        else:
            # Retrace requires a prior-route relationship; not current action material.
            continue
        result[key].append({
            'id': use['id'], 'meaning': meaning,
            'from_pin_s': use['from_s'], 'to_pin_s': use['to_s'],
            'event_at_pin_s': use.get('at_s'),
            'relation': '행동핀 시점에 유효한 산책 이동 상황. 행동의 원인이나 지속시간은 아님',
        })
    for key in result:
        if len({x['meaning'] for x in result[key]}) > 1:
            result[key] = []
    return result
