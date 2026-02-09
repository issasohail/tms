(function ($) {
    $(document).on('click', 'button.rotate', function (e) {
        e.preventDefault();
        const $btn = $(this);
        const field = $btn.data('field');
        const dir = $btn.data('dir');
        const parts = window.location.pathname.split('/');
        const pk = parts[parts.length - 3];

        $.post(`${parts.slice(0, -2).join('/')}${pk}/rotate/${field}/${dir}/`, {
            csrfmiddlewaretoken: $('input[name=csrfmiddlewaretoken]').val()
        }).done(resp => {
            if (resp.success) window.location.reload();
            else alert('Rotation failed');
        });
    });
})(django.jQuery);
