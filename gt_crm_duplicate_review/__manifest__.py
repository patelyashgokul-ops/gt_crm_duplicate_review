{
    'name': 'CRM Duplicate Review',
    'version': '20.0.2.0.0',
    'summary': 'Review exact CRM lead and opportunity duplicate candidates',
    'author': 'Golok Technology',
    'category': 'CRM',
    'license': 'OPL-1',
    'depends': ['crm'],
    'data': [
        'security/duplicate_review_groups.xml',
        'security/ir.access.csv',
        'views/duplicate_case_views.xml',
    ],
    'installable': True,
    'application': False,
}
