#LIBRARIES
import time
import json
import polib

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from collections import OrderedDict

#FLUENT
from .models import MasterTranslation, Translation
from . import cldr
from .cldr.rules import LANGUAGE_LOOKUPS


def export_translations_as_arb(masters, language_code=settings.LANGUAGE_CODE):
    response = HttpResponse(content_type="application/arb")
    response['Content-Disposition'] = 'attachment; filename="translations.arb"'

    data = OrderedDict()
    data["@@locale"] = language_code
    data["@@last_modified"] = timezone.now().strftime("%Y-%m-%dT%H:%M") + str.format('{0:+06.2f}', float(time.timezone) / 3600)

    for master in masters:
        key = str(master.pk)

        data[key] = cldr.export_master_message(master)
        data["@" + key] = {
            "type": "text",
            "source_text": cldr._icu_encode(master.text),
            "context": master.hint
        }

    response.write(json.dumps(data, indent=4))
    return response

'''
def export_translations_as_csv(masters):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="translations.csv"'

    writer = csv.writer(response, delimiter=",", quotechar='"')

    headings_written = False
    headings = []

    for trans in masters:
        row = [ trans.text.encode("utf-8"), trans.hint ]
        headings = [ "Text", "Context"]
        for code, desc in settings.LANGUAGES:
            if not headings_written: headings.append(code)

            if code in trans.translated_into_languages:
                row.append(trans.translations.get(language_code=code).translated_text.encode("utf-8"))

            else:
                row.append("")

        if not headings_written:
            writer.writerow(headings)
            headings_written = True
        writer.writerow(row)
    return response
'''


def get_used_fields(plurals, language_code):
    lookup = LANGUAGE_LOOKUPS[language_code]
    missing = set(lookup.plurals_used) - set(plurals)
    if missing:
        RK = dict((v, k) for (k, v) in cldr.ICU_KEYWORDS.items())
        raise ValueError("Missing keywords required by language: %s" % ', '.join(map(RK.get, missing)))
    return dict((keyword, form)
        for (keyword, form) in plurals.items()
        if keyword.startswith('=') or keyword in lookup.plurals_used
    )


def import_translations_from_arb(file_in, language_code):
    """ ARB is json with original translations (which we ignore) and translated data which is
        provided as icu translated strings.

        The data keys should match our MasterTranslation pk's.
    """
    data = json.loads(file_in.read())
    errors = []

    for k, v in data.iteritems():
        if k.startswith("@") and not k.startswith("@@"):
            pk = k.lstrip("@")
            try:
                master = MasterTranslation.objects.get(pk=pk)
            except MasterTranslation.DoesNotExist:
                errors.append(("Could not find translation: {0}".format(v['source_text']), 'unknown', ""))
                continue

            try:
                plurals = cldr.import_icu_message(data[str(pk)])
                if master.is_plural:
                    plurals = get_used_fields(plurals, language_code)
            except ValueError, e:
                errors.append((e.message, master.text, data[str(pk)]))
                continue

            trans_errors = master.create_or_update_translation(language_code, singular_text=None, plural_texts=plurals, validate=True)
            if trans_errors:
                errors.extend(trans_errors)
                continue
    return errors


def import_translations_from_po(file_contents, language_code, from_language):
    """ PO are standard 'pot' files for translations, we parse them using polib.

        msgids that are not already known to fluent will be skipped.
    """
    if isinstance(file_contents, str):
        # if we're passing in a str, convert to unicode
        file_contents = unicode(file_contents, 'utf-8')

    pofile = polib.pofile(file_contents, encoding='utf-8')
    errors = []

    lookup = LANGUAGE_LOOKUPS[language_code]

    for entry in pofile:
        pk = MasterTranslation.generate_key(entry.msgid, entry.msgctxt or '', from_language)
        try:
            master = MasterTranslation.objects.get(pk=pk)
        except MasterTranslation.DoesNotExist:
            errors.append((u"Could not find translation: {}, {}".format(repr(entry.msgid), repr(entry.msgctxt)), 'unknown', ""))
            continue

        if master.is_plural:
            singular_text, plural_texts = None, {}

            # Makesure the translation specifies the same number of gettext forms we expect to see
            #FIXME: see what happens when the po file misses a translation
            _defined, _expected = len(entry.msgstr_plural), len(lookup.gettext_forms)
            if _defined != _expected:
                errors.append((u"Translations are missing, we require {} plural forms, only found {}", _expected, _defined))
                continue

            # Assign each indexed gettext translation to the matching form codeword
            for indx, forms in lookup.gettext_forms.items():
                for f in forms:
                    plural_texts[f] = entry.msgstr_plural[indx]

        else:
            plural_texts, singular_text = None, entry.msgstr

        trans_errors = master.create_or_update_translation(language_code, singular_text=singular_text, plural_texts=plural_texts, validate=True)
        if trans_errors:
            errors.extend(trans_errors)
            continue

    return errors


def export_translations_to_po(language_code):
    lookup = LANGUAGE_LOOKUPS[language_code]

    pofile = polib.POFile()
    for master in MasterTranslation.objects.all():
        entry = polib.POEntry(msgid=master.text, comment=master.hint)
        if language_code in master.translations_by_language_code:
            translation = Translation.objects.get(pk=master.translations_by_language_code[language_code])
        else:
            translation = Translation.objects.get(pk=master.translations_by_language_code[master.language_code])

        if master.is_plural:
            plural_texts = {}
            for indx, forms in lookup.gettext_forms.items():
                for f in forms:
                    plural_texts[indx] = translation.plural_texts[f]
            entry.msgstr_plural = plural_texts
            entry.msgstr = None
        else:
            entry.msgstr = translation.text
        pofile.append(entry)

    response = HttpResponse(pofile, content_type="text/plain")
    response['Content-Disposition'] = "attachment; filename=django.po"
    return response


class OutputFormat:
    ARB = 'ARB'
    PO = 'PO'


def _export_master_translations_to_pot(masters, language_code):
    pofile = polib.POFile()
    for master in masters:
        entry = polib.POEntry(msgid=master.text, comment=master.hint)
        pofile.append(entry)

    response = HttpResponse(pofile, content_type="text/plain")
    response['Content-Disposition'] = "attachment; filename=django.pot"
    return response


def _export_master_translations_to_arb(masters, language_code):
    return export_translations_as_arb(masters, language_code)


def export_master_translations(masters, language_code=settings.LANGUAGE_CODE, output_format=OutputFormat.ARB):
    if any(x for x in masters if x.language_code != language_code):
        raise ValueError("Some of the specified master translations don't match the passed language_code")

    if output_format == OutputFormat.PO:
        return _export_master_translations_to_pot(masters, language_code)
    else:
        return _export_master_translations_to_arb(masters, language_code)
