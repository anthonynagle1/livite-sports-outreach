#!/usr/bin/env python3
"""
Tool: discover_opponent_url.py
Purpose: Automatically discover NCAA athletics website URL for a school

Uses web search to find official athletics sites for opponent schools.

Usage:
    python tools/discover_opponent_url.py --school "Boston University"

Output: JSON with athletics URL
"""

import argparse
import json
import sys
import re
from urllib.parse import urlparse


def discover_athletics_url(school_name):
    """
    Discover athletics website URL for a school using common patterns.

    Args:
        school_name (str): School name (e.g., "Boston University", "Harvard")

    Returns:
        dict: Result with URL or error
    """
    school_lower = school_name.lower()

    # Common NCAA athletics URL patterns
    patterns = {
        # Major schools with dedicated athletics domains
        'boston college': 'https://bceagles.com',
        'boston university': 'https://goterriers.com',
        'harvard': 'https://gocrimson.com',
        'yale': 'https://yalebulldogs.com',
        'dartmouth': 'https://dartmouthsports.com',
        'brown': 'https://brownbears.com',
        'columbia': 'https://gocolumbialions.com',
        'cornell': 'https://cornellbigred.com',
        'penn': 'https://pennathletics.com',
        'princeton': 'https://goprincetontigers.com',
        'merrimack': 'https://merrimackathletics.com',
        'northeastern': 'https://gonu.com',
        'uconn': 'https://uconnhuskies.com',
        'connecticut': 'https://uconnhuskies.com',
        'university of connecticut': 'https://uconnhuskies.com',
        'umass': 'https://umassathletics.com',
        'umass lowell': 'https://goriverhawks.com',
        'maine': 'https://goblackbears.com',
        'university of maine': 'https://goblackbears.com',
        'university of new hampshire': 'https://unhwildcats.com',
        'vermont': 'https://uvmathletics.com',
        'university of vermont': 'https://uvmathletics.com',
        'providence': 'https://friars.com',
        'providence college': 'https://friars.com',

        # ACC Schools
        'duke': 'https://goduke.com',
        'duke university': 'https://goduke.com',
        'syracuse': 'https://cuse.com',
        'syracuse university': 'https://cuse.com',
        'notre dame': 'https://fightingirish.com',
        'miami': 'https://hurricanesports.com',
        'virginia': 'https://virginiasports.com',
        'university of virginia': 'https://virginiasports.com',
        'vmi': 'https://vmikeydets.com',
        'virginia military institute': 'https://vmikeydets.com',
        'virginia tech': 'https://hokiesports.com',
        'clemson': 'https://clemsontigers.com',
        'georgia tech': 'https://ramblinwreck.com',
        'wake forest': 'https://godeacs.com',
        'stanford': 'https://gostanford.com',
        'california': 'https://calbears.com',
        'northwestern': 'https://nusports.com',
        'pittsburgh': 'https://pittsburghpanthers.com',
        'florida state': 'https://seminoles.com',
        'florida state university': 'https://seminoles.com',

        # Other common schools
        'sacred heart': 'https://sacredheartpioneers.com',
        'bryant': 'https://bryantbulldogs.com',
        'bryant university': 'https://bryantbulldogs.com',
        'njit': 'https://njithighlanders.com',
        'new jersey institute of technology': 'https://njithighlanders.com',

        # Patriot League
        'bucknell': 'https://bucknellbison.com',
        'colgate': 'https://gocolgateraiders.com',
        'holy cross': 'https://goholycross.com',
        'loyola maryland': 'https://loyolagreyhounds.com',
        'lehigh': 'https://lehighsports.com',
        'lehigh university': 'https://lehighsports.com',
        'lafayette': 'https://goleopards.com',
        'lafayette college': 'https://goleopards.com',
        'army': 'https://goarmywestpoint.com',
        'army west point': 'https://goarmywestpoint.com',
        'navy': 'https://navysports.com',
        'navy midshipmen': 'https://navysports.com',
        'naval academy': 'https://navysports.com',
        'american': 'https://aueagles.com',
        'american university': 'https://aueagles.com',

        # MAAC & Other Northeast
        'siena': 'https://sienasaints.com',
        'siena college': 'https://sienasaints.com',
        'marist': 'https://goredfoxes.com',
        'marist college': 'https://goredfoxes.com',
        'quinnipiac': 'https://gobobcats.com',
        'quinnipiac university': 'https://gobobcats.com',
        'stonehill': 'https://stonehillskyhawks.com',
        'stonehill college': 'https://stonehillskyhawks.com',
        'mit': 'https://mitathletics.com',
        'massachusetts institute of technology': 'https://mitathletics.com',
        'rhode island': 'https://gorhody.com',
        'university of rhode island': 'https://gorhody.com',
        'new hampshire': 'https://unhwildcats.com',

        # Boston-area schools
        'tufts': 'https://gotuftsjumbos.com',
        'tufts university': 'https://gotuftsjumbos.com',
        'bentley': 'https://bentleyfalcons.com',
        'bentley university': 'https://bentleyfalcons.com',

        # NESCAC D3 schools (Tufts opponents)
        'amherst': 'https://athletics.amherst.edu',
        'amherst college': 'https://athletics.amherst.edu',
        'bates': 'https://gobatesbobcats.com',
        'bates college': 'https://gobatesbobcats.com',
        'bowdoin': 'https://athletics.bowdoin.edu',
        'bowdoin college': 'https://athletics.bowdoin.edu',
        'colby': 'https://athletics.colby.edu',
        'colby college': 'https://athletics.colby.edu',
        'connecticut college': 'https://camelsonline.com',
        'hamilton': 'https://athletics.hamilton.edu',
        'hamilton college': 'https://athletics.hamilton.edu',
        'middlebury': 'https://athletics.middlebury.edu',
        'middlebury college': 'https://athletics.middlebury.edu',
        'trinity': 'https://bantamsports.com',
        'trinity college': 'https://bantamsports.com',
        'wesleyan': 'https://www.wesleyanathletics.com',
        'wesleyan university': 'https://www.wesleyanathletics.com',
        'williams': 'https://ephsports.williams.edu',
        'williams college': 'https://ephsports.williams.edu',

        # D2 NE-10 schools (Bentley opponents)
        'assumption': 'https://assumptiongreyhounds.com',
        'assumption university': 'https://assumptiongreyhounds.com',
        'saint anselm': 'https://saintanselmhawks.com',
        'saint anselm college': 'https://saintanselmhawks.com',
        "saint michael's": 'https://smcathletics.com',
        "saint michael's college": 'https://smcathletics.com',
        'southern new hampshire': 'https://www.snhupenmen.com',
        'southern new hampshire university': 'https://www.snhupenmen.com',
        'pace': 'https://paceuathletics.com',
        'pace university': 'https://paceuathletics.com',
        'adelphi': 'https://aupanthers.com',
        'adelphi university': 'https://aupanthers.com',
        'franklin pierce': 'https://fpuravens.com',
        'franklin pierce university': 'https://fpuravens.com',
        'felician': 'https://felicianathletics.com',
        'felician university': 'https://felicianathletics.com',
        'gwynedd mercy': 'https://gwyneddathletics.com',
        'gwynedd mercy university': 'https://gwyneddathletics.com',
        'mercy': 'https://gwyneddathletics.com',
        'mercy university': 'https://gwyneddathletics.com',
        'kutztown': 'https://kubears.com',
        'kutztown university': 'https://kubears.com',
        'west chester': 'https://wcuathletics.com',
        'west chester university of pennsylvania': 'https://wcuathletics.com',

        # Other New England D3
        'endicott': 'https://ecgulls.com',
        'endicott college': 'https://ecgulls.com',
        'emmanuel': 'https://goecsaints.com',
        'emmanuel college': 'https://goecsaints.com',
        'emmanuel college (mass.)': 'https://goecsaints.com',
        'johnson & wales': 'https://providence.jwuathletics.com',
        'johnson & wales university': 'https://providence.jwuathletics.com',
        'roger williams': 'https://rwuhawks.com',
        'roger williams university': 'https://rwuhawks.com',
        'russell sage': 'https://sagegators.com',
        'russell sage colleges': 'https://sagegators.com',
        'suffolk': 'https://www.gosuffolkrams.com',
        'suffolk university': 'https://www.gosuffolkrams.com',
        'wpi': 'https://athletics.wpi.edu',
        'worcester polytechnic institute': 'https://athletics.wpi.edu',
        'union': 'https://unionathletics.com',
        'union college': 'https://unionathletics.com',
        'hobart': 'https://hwsathletics.com',
        'hobart college': 'https://hwsathletics.com',
        'wheaton': 'https://wheatoncollegelyons.com',
        'wheaton college': 'https://wheatoncollegelyons.com',
        'university of chicago': 'https://athletics.uchicago.edu',
        'colorado college': 'https://cctigers.com',

        # Military
        'air force': 'https://goairforcefalcons.com',

        # CAA / Colonial Athletic Association
        'towson': 'https://towsontigers.com',
        'towson university': 'https://towsontigers.com',
        'elon': 'https://elonphoenix.com',
        'elon university': 'https://elonphoenix.com',
        'monmouth': 'https://monmouthhawks.com',
        'monmouth university': 'https://monmouthhawks.com',
        'hofstra': 'https://gohofstra.com',
        'hofstra university': 'https://gohofstra.com',
        'stony brook': 'https://stonybrookathletics.com',
        'stony brook university': 'https://stonybrookathletics.com',
        'william & mary': 'https://tribeathletics.com',
        'college of william & mary': 'https://tribeathletics.com',
        'drexel': 'https://drexeldragons.com',
        'drexel university': 'https://drexeldragons.com',
        'charleston': 'https://cofcsports.com',
        'college of charleston': 'https://cofcsports.com',
        'hampton': 'https://hamptonpirates.com',
        'hampton university': 'https://hamptonpirates.com',

        # Schools that need explicit entries to avoid incorrect partial matches
        'american international': 'https://aicyellowjackets.com',
        'american international college': 'https://aicyellowjackets.com',
        'penn state': 'https://gopsusports.com',
        'penn state university': 'https://gopsusports.com',
        'pennsylvania state university': 'https://gopsusports.com',

        # Regional state universities
        'umass boston': 'https://www.beaconsathletics.com',
        'eastern connecticut state': 'https://gowarriorathletics.com',
        'southern connecticut state': 'https://southernctowls.com',
        'southern connecticut state university': 'https://southernctowls.com',
        'western connecticut state': 'https://www.wcsuathletics.com',
        'rhode island college': 'https://goanchormen.com',
        'salem state': 'https://salemstatevikings.com',
        'salem state university': 'https://salemstatevikings.com',

        # D3 NEWMAC / NE Women's & Men's Athletic Conference
        'smith': 'https://smithpioneers.com',
        'smith college': 'https://smithpioneers.com',
        'mount holyoke': 'https://athletics.mtholyoke.edu',
        'mount holyoke college': 'https://athletics.mtholyoke.edu',
        'springfield': 'https://springfieldcollegepride.com',
        'springfield college': 'https://springfieldcollegepride.com',
        'simmons': 'https://athletics.simmons.edu',
        'simmons university': 'https://athletics.simmons.edu',
        'wellesley': 'https://wellesleyblue.com',
        'wellesley college': 'https://wellesleyblue.com',
        'babson': 'https://babsonathletics.com',
        'babson college': 'https://babsonathletics.com',
        'emerson': 'https://emersonlions.com',
        'emerson college': 'https://emersonlions.com',
        'clark': 'https://clarkathletics.com',
        'clark university': 'https://clarkathletics.com',
        'gordon': 'https://athletics.gordon.edu',
        'gordon college': 'https://athletics.gordon.edu',
        'coast guard': 'https://coastguardathletics.com',
        'coast guard academy': 'https://coastguardathletics.com',
        'united states coast guard academy': 'https://coastguardathletics.com',

        # Additional Boston-area D3 schools
        'wentworth': 'https://www.wentworthathletics.com',
        'wentworth institute of technology': 'https://www.wentworthathletics.com',
        'curry': 'https://www.curryathletics.com',
        'curry college': 'https://www.curryathletics.com',
        'lasell': 'https://laserpride.lasell.edu',
        'lasell university': 'https://laserpride.lasell.edu',
        'regis': 'https://www.goregispride.com',
        'regis college': 'https://www.goregispride.com',

        # GNAC / CNE / Other NE D3
        'albertus magnus': 'https://www.albertusfalcons.com',
        'albertus magnus college': 'https://www.albertusfalcons.com',
        'colby-sawyer': 'https://www.colby-sawyerathletics.com',
        'colby-sawyer college': 'https://www.colby-sawyerathletics.com',
        'dean': 'https://deanbulldogs.com',
        'dean college': 'https://deanbulldogs.com',
        'framingham state': 'https://www.fsurams.com',
        'framingham state university': 'https://www.fsurams.com',
        'new england college': 'https://athletics.nec.edu',
        'norwich': 'https://norwichathletics.com',
        'norwich university': 'https://norwichathletics.com',
        'rivier': 'https://rivierathletics.com',
        'rivier university': 'https://rivierathletics.com',
        'saint joseph': 'https://www.usjbluejays.com',
        'university of saint joseph': 'https://www.usjbluejays.com',
        'worcester state': 'https://www.wsulancers.com',
        'worcester state university': 'https://www.wsulancers.com',
        'nyu': 'https://gonyuathletics.com',
        'new york university': 'https://gonyuathletics.com',
        'illinois tech': 'https://illinoistechathletics.com',
        'illinois institute of technology': 'https://illinoistechathletics.com',
        'elms': 'https://www.ecblazers.com',
        'elms college': 'https://www.ecblazers.com',
        'fisher': 'https://www.fisherfalcons.com',
        'fisher college': 'https://www.fisherfalcons.com',
        'fisher college (mass.)': 'https://www.fisherfalcons.com',
        'mcla': 'https://athletics.mcla.edu',
        'massachusetts college of liberal arts': 'https://athletics.mcla.edu',
        'nichols': 'https://nicholsathletics.com',
        'nichols college': 'https://nicholsathletics.com',
        'oneonta': 'https://oneontaathletics.com',
        'suny oneonta': 'https://oneontaathletics.com',
        'thomas': 'https://athletics.thomas.edu',
        'thomas college': 'https://athletics.thomas.edu',
        'castleton': 'https://castletonsports.com',
        'vermont state university castleton': 'https://castletonsports.com',

        # D3 NESCAC / Liberty League / Other Northeast
        'skidmore': 'https://skidmoreathletics.com',
        'skidmore college': 'https://skidmoreathletics.com',
        'st. lawrence': 'https://saintsathletics.com',
        'st lawrence': 'https://saintsathletics.com',
        'st. lawrence university': 'https://saintsathletics.com',
        'salve regina': 'https://salveathletics.com',
        'salve regina university': 'https://salveathletics.com',

        # Brandeis & Lesley (user's target schools)
        'brandeis': 'https://brandeisjudges.com',
        'brandeis university': 'https://brandeisjudges.com',
        'lesley': 'https://lesleyathletics.com',
        'lesley university': 'https://lesleyathletics.com',

        # NEC Conference (Stonehill opponents)
        'wagner': 'https://wagnerathletics.com',
        'wagner college': 'https://wagnerathletics.com',
        'le moyne': 'https://lemoynedolphins.com',
        'le moyne college': 'https://lemoynedolphins.com',
        'saint francis': 'https://sfuathletics.com',
        'saint francis university': 'https://sfuathletics.com',
        'st. francis': 'https://sfuathletics.com',
        'mercyhurst': 'https://hurstathletics.com',
        'mercyhurst university': 'https://hurstathletics.com',
        'lindenwood': 'https://lindenwoodlions.com',
        'lindenwood university': 'https://lindenwoodlions.com',
        'liu': 'https://liuathletics.com',
        'long island university': 'https://liuathletics.com',
        'chicago state': 'https://gocsucougars.com',
        'chicago state university': 'https://gocsucougars.com',
        'delaware state': 'https://dsuhornets.com',
        'delaware state university': 'https://dsuhornets.com',
        'new haven': 'https://newhavenchargers.com',
        'university of new haven': 'https://newhavenchargers.com',
        'simon fraser': 'https://athletics.sfu.ca',
        'simon fraser university': 'https://athletics.sfu.ca',

        # D3 Midwest
        'wooster': 'https://woosterathletics.com',
        'the college of wooster': 'https://woosterathletics.com',
        'college of wooster': 'https://woosterathletics.com',
        'kalamazoo': 'https://hornets.kzoo.edu',
        'kalamazoo college': 'https://hornets.kzoo.edu',
        'depauw': 'https://depauwtigers.com',
        'depauw university': 'https://depauwtigers.com',
        'uw-la crosse': 'https://uwlathletics.com',
        'university of wisconsin-la crosse': 'https://uwlathletics.com',
        'wisconsin-la crosse': 'https://uwlathletics.com',
        'uw-oshkosh': 'https://uwoshkoshtitans.com',
        'university of wisconsin-oshkosh': 'https://uwoshkoshtitans.com',
        'wisconsin-oshkosh': 'https://uwoshkoshtitans.com',

        # Vermont State University system (merged campuses)
        'vtsu lyndon': 'https://vtsuhornets.com',
        'vermont state lyndon': 'https://vtsuhornets.com',
        'vtsu johnson': 'https://www.vtsubadgers.com',
        'vermont state johnson': 'https://www.vtsubadgers.com',
        'vermont state university lyndon': 'https://vtsuhornets.com',
        'vermont state university johnson': 'https://www.vtsubadgers.com',

        # Maine system abbreviations
        'me.-presque isle': 'https://owls.umpi.edu',
        'umaine-presque isle': 'https://owls.umpi.edu',
        'umaine presque isle': 'https://owls.umpi.edu',
        'maine presque isle': 'https://owls.umpi.edu',
        'umpi': 'https://owls.umpi.edu',
        'university of maine - fort kent': 'https://athletics.umfk.edu',
        'me.-fort kent': 'https://athletics.umfk.edu',
        'umaine fort kent': 'https://athletics.umfk.edu',
        'umfk': 'https://athletics.umfk.edu',
        'umaine farmington': 'https://goumfbeavers.com',
        'maine farmington': 'https://goumfbeavers.com',
        'umaine-farmington': 'https://goumfbeavers.com',
        'umf': 'https://goumfbeavers.com',

        # SUNY system
        'suny cobleskill': 'https://fightingtigers.cobleskill.edu',

        # New England regional schools (abbreviations)
        'western new eng.': 'https://wnegoldenbears.com',
        'western new england': 'https://wnegoldenbears.com',
        'western new england university': 'https://wnegoldenbears.com',
        'new england col.': 'https://athletics.nec.edu',
        'salem st.': 'https://salemstatevikings.com',
        'bay path': 'https://athletics.baypath.edu',
        'bay path university': 'https://athletics.baypath.edu',

        # Saint Joseph variations (Connecticut vs Maine)
        "saint joseph's (me.)": 'https://sjcmehuskies.com',
        "saint joseph's (maine)": 'https://sjcmehuskies.com',
        'saint joseph of maine': 'https://sjcmehuskies.com',
        'st. joseph (maine)': 'https://sjcmehuskies.com',
        'saint joseph (conn.)': 'https://www.usjbluejays.com',
        'university of saint joseph (conn.)': 'https://www.usjbluejays.com',
        'usj (conn.)': 'https://www.usjbluejays.com',

        # Washington University (St. Louis)
        'washington u.': 'https://washubears.com',
        'washington university': 'https://washubears.com',
        'washington university in st. louis': 'https://washubears.com',
        'wash u': 'https://washubears.com',

        # Other missing schools
        'buena vista': 'https://bvuathletics.com',
        'buena vista university': 'https://bvuathletics.com',
        'bryant & stratton': 'https://albany.bscbobcats.com',
        'bryant & stratton - albany': 'https://albany.bscbobcats.com',

        # Assumption (short form)
        'assumption': 'https://assumptiongreyhounds.com',

        # Additional D3 schools (common opponents)
        'manhattanville': 'https://govaliants.com',
        'manhattanville college': 'https://govaliants.com',
        'manhattanville university': 'https://govaliants.com',
        'utica': 'https://uticapioneers.com',
        'utica university': 'https://uticapioneers.com',
        'utica college': 'https://uticapioneers.com',
        'chatham': 'https://gochathamcougars.com',
        'chatham university': 'https://gochathamcougars.com',
        'saint vincent': 'https://athletics.stvincent.edu',
        'saint vincent college': 'https://athletics.stvincent.edu',
        'fitchburg state': 'https://www.fitchburgfalcons.com',
        'fitchburg state university': 'https://www.fitchburgfalcons.com',
        'bridgewater state': 'https://bsubears.com',
        'bridgewater state university': 'https://bsubears.com',
        'westfield state': 'https://wscowls.com',
        'westfield state university': 'https://wscowls.com',
        'keene state': 'https://keeneowls.com',
        'keene state college': 'https://keeneowls.com',
        'plymouth state': 'https://athletics.plymouth.edu',
        'plymouth state university': 'https://athletics.plymouth.edu',
        'anna maria': 'https://goamcats.com',
        'anna maria college': 'https://goamcats.com',
        'southern maine': 'https://usmsports.com',
        'university of southern maine': 'https://usmsports.com',
        'husson': 'https://hussoneagles.com',
        'husson university': 'https://hussoneagles.com',
        'mitchell': 'https://mitchellmariners.com',
        'mitchell college': 'https://mitchellmariners.com',
        "st. joseph's": 'https://sjcmehuskies.com',
        'castleton state': 'https://castletonsports.com',
        'johnson state': 'https://www.vtsubadgers.com',
        'lyndon state': 'https://vtsuhornets.com',

        # Backfill batch — schools missing from opponent games
        'ohio northern': 'https://www.onusports.com',
        'ohio northern university': 'https://www.onusports.com',
        'marywood': 'https://marywoodpacers.com',
        'marywood university': 'https://marywoodpacers.com',
        'juniata': 'https://www.juniatasports.net',
        'juniata college': 'https://www.juniatasports.net',
        'haverford': 'https://haverfordathletics.com',
        'haverford college': 'https://haverfordathletics.com',
        'hartford': 'https://hartfordhawks.com',
        'university of hartford': 'https://hartfordhawks.com',
        'framingham st.': 'https://www.fsurams.com',
        'blackburn': 'https://blackburnbeavers.com',
        'blackburn college': 'https://blackburnbeavers.com',
        'la sierra': 'https://lsugoldeneagles.com',
        'la sierra university': 'https://lsugoldeneagles.com',
        'st. scholastica': 'https://csssaints.com',
        'college of st. scholastica': 'https://csssaints.com',
        'emory': 'https://emoryathletics.com',
        'emory university': 'https://emoryathletics.com',
        'hunter': 'https://www.huntercollegeathletics.com',
        'hunter college': 'https://www.huntercollegeathletics.com',
        'sarah lawrence': 'https://gogryphons.com',
        'sarah lawrence college': 'https://gogryphons.com',
        'hartwick': 'https://www.hartwickhawks.com',
        'hartwick college': 'https://www.hartwickhawks.com',
        'heidelberg': 'https://www.bergathletics.com',
        'heidelberg university': 'https://www.bergathletics.com',
        'marquette': 'https://gomarquette.com',
        'marquette university': 'https://gomarquette.com',
        'bloomfield': 'https://bcbearsathletics.com',
        'bloomfield university': 'https://bcbearsathletics.com',
        'byu': 'https://byucougars.com',
        'brigham young university': 'https://byucougars.com',
        'suny delhi': 'https://delhibroncos.com',
        'villanova': 'https://villanova.com',
        'villanova university': 'https://villanova.com',
        'michigan': 'https://mgoblue.com',
        'university of michigan': 'https://mgoblue.com',
        'fitchburg st.': 'https://www.fitchburgfalcons.com',
        'vassar': 'https://www.vassarathletics.com',
        'vassar college': 'https://www.vassarathletics.com',
        'washington adventist': 'https://www.wauathletics.com',
        'washington adventist university': 'https://www.wauathletics.com',
        'rutgers-newark': 'https://rutgersnewarkathletics.com',
        'rit': 'https://ritathletics.com',
        'rochester institute of technology': 'https://ritathletics.com',
        'farmingdale state': 'https://farmingdalesports.com',
        'farmingdale state college': 'https://farmingdalesports.com',
        'washington univ.': 'https://washubears.com',
        'maine maritime': 'https://marinersports.org',
        'maine maritime academy': 'https://marinersports.org',
    }

    # Check if we have a known pattern
    if school_lower in patterns:
        return {
            'school': school_name,
            'athletics_url': patterns[school_lower],
            'confidence': 'high',
            'method': 'known_pattern',
            'success': True
        }

    # Try partial matches
    for key, url in patterns.items():
        if key in school_lower or school_lower in key:
            return {
                'school': school_name,
                'athletics_url': url,
                'confidence': 'medium',
                'method': 'partial_match',
                'success': True
            }

    # No match found
    return {
        'school': school_name,
        'athletics_url': None,
        'confidence': 'none',
        'method': 'not_found',
        'success': False,
        'error': f'No athletics URL pattern found for {school_name}'
    }


def main():
    parser = argparse.ArgumentParser(
        description="Discover athletics website URL for a school"
    )
    parser.add_argument(
        "--school",
        required=True,
        help="School name (e.g., 'Boston University', 'Harvard')"
    )
    parser.add_argument(
        "--output",
        help="Output JSON file path (default: stdout)"
    )

    args = parser.parse_args()

    # Discover URL
    result = discover_athletics_url(args.school)

    # Save to file if specified
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        if result['success']:
            print(f"✓ Found URL for {args.school}: {result['athletics_url']}", file=sys.stderr)
        else:
            print(f"✗ Could not find URL for {args.school}", file=sys.stderr)

    # Always output JSON to stdout for pipeline processing
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
