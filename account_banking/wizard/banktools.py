# -*- encoding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2009 EduSense BV (<http://www.edusense.nl>).
#    All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import sys
import datetime
import re
from tools.translate import _
from account_banking.parsers import convert
from account_banking import sepa
from account_banking.struct import struct

__all__ = [
    'get_period', 
    'get_bank_account',
    'get_or_create_partner',
    'get_company_bank_account',
    'create_bank_account',
]

def get_period(pool, cursor, uid, date, company, log):
    '''
    Get a suitable period for the given date range and the given company.
    '''
    fiscalyear_obj = pool.get('account.fiscalyear')
    period_obj = pool.get('account.period')
    if not date:
        date = convert.date2str(datetime.datetime.today())

    search_date = convert.date2str(date)
    fiscalyear_ids = fiscalyear_obj.search(cursor, uid, [
        ('date_start','<=', search_date), ('date_stop','>=', search_date),
        ('state','=','draft'), ('company_id','=',company.id)
    ])
    if not fiscalyear_ids:
        fiscalyear_ids = fiscalyear_obj.search(cursor, uid, [
            ('date_start','<=',search_date), ('date_stop','>=',search_date),
            ('state','=','draft'), ('company_id','=',None)
        ])
    if not fiscalyear_ids:
        log.append(
            _('No suitable fiscal year found for company %(company_name)s')
            % dict(company_name=company.name)
        )
        return False
    elif len(fiscalyear_ids) > 1:
        log.append(
            _('Multiple overlapping fiscal years found for date %(date)s')
            % dict(date=date)
        )
        return False

    fiscalyear_id = fiscalyear_ids[0]
    period_ids = period_obj.search(cursor, uid, [
        ('date_start','<=',search_date), ('date_stop','>=',search_date),
        ('fiscalyear_id','=',fiscalyear_id), ('state','=','draft')
    ])
    if not period_ids:
        log.append(_('No suitable period found for date %(date)s')
                   % dict(date=date)
        )
        return False
    if len(period_ids) != 1:
        log.append(_('Multiple overlapping periods for date %(date)s')
                   % dict(date=date)
        )
        return False
    return period_ids[0]

def get_bank_account(pool, cursor, uid, account_number, log, fail=False):
    '''
    Get the bank account with account number account_number
    '''
    # No need to search for nothing
    if not account_number:
        return False

    partner_bank_obj = pool.get('res.partner.bank')
    bank_account_ids = partner_bank_obj.search(cursor, uid, [
        ('acc_number', '=', account_number)
    ])
    if not bank_account_ids:
        bank_account_ids = partner_bank_obj.search(cursor, uid, [
            ('iban', '=', account_number)
        ])
    if not bank_account_ids:
        if not fail:
            log.append(
                _('Bank account %(account_no)s was not found in the database')
                % dict(account_no=account_number)
            )
        return False
    elif len(bank_account_ids) != 1:
        log.append(
            _('More than one bank account was found with the same number %(account_no)s')
            % dict(account_no=account_number)
        )
        return False
    return partner_bank_obj.browse(cursor, uid, bank_account_ids)[0]

def get_or_create_partner(pool, cursor, uid, name, log):
    '''
    Get or create the partner belonging to the account holders name <name>
    '''
    partner_obj = pool.get('res.partner')
    partner_ids = partner_obj.search(cursor, uid, [('name', 'ilike', name)])
    if not partner_ids:
        partner_id = partner_obj.create(cursor, uid, dict(
            name=name, active=True, comment='Generated by Import Bank Statements File',
        ))
    elif len(partner_ids) > 1:
        log.append(
            _('More then one possible match found for partner with name %(name)s')
            % {'name': name}
        )
        return False
    else:
        partner_id = partner_ids[0]
    return partner_obj.browse(cursor, uid, partner_id)[0]

def get_company_bank_account(pool, cursor, uid, account_number,
                             company, log):
    '''
    Get the matching bank account for this company.
    '''
    results = struct()
    bank_account = get_bank_account(pool, cursor, uid, account_number, log,
                                    fail=True)
    if not bank_account:
        return False
    if bank_account.partner_id.id != company.partner_id.id:
        log.append(
            _('Account %(account_no)s is not owned by %(partner)s')
            % dict(account_no = account_number,
                   partner = company.partner_id.name,
        ))
        return False
    results.account = bank_account
    bank_settings_obj = pool.get('account.banking.account.settings')
    bank_settings_ids = bank_settings_obj.search(cursor, uid, [
        ('partner_bank_id', '=', bank_account.id)
    ])
    if bank_settings_ids:
        settings = bank_settings_obj.browse(cursor, uid, bank_settings_ids)[0]
        results.journal_id = settings.journal_id
        results.default_debit_account_id = settings.default_debit_account_id
        results.default_credit_account_id = settings.default_credit_account_id
    return results

def get_or_create_bank(pool, cursor, uid, bic, online=True):
    '''
    Find or create the bank with the provided BIC code.
    When online, the SWIFT database will be consulted in order to
    provide for missing information.
    '''
    bank_obj = pool.get('res.bank')

    # Self generated key?
    if len(bic) < 8:
        # search key
        bank_ids = bank_obj.search(
            cursor, uid, [
                ('code', '=', bic[:6])
            ])
        if not bank_ids:
            bank_ids = bank_obj.search(
                cursor, uid, [
                    ('bic', 'ilike', bic + '%')
                ])
    else:
        bank_ids = bank_obj.search(
            cursor, uid, [
                ('bic', '=', bic)
            ])

    if bank_ids and len(bank_ids) == 1:
        banks = bank_obj.browse(cursor, uid, bank_ids)
        return banks[0].id, banks[0].country.id

    country_obj = pool.get('res.country')
    country_ids = country_obj.search(
        cursor, uid, [('code', '=', bic[4:6])]
    )
    if online:
        info, address = sepa.online.bank_info(bic)
        if info:
            bank_id = bank_obj.create(cursor, uid, dict(
                code = info.code,
                name = info.name,
                street = address.street,
                street2 = address.street2,
                zip = address.zip,
                city = address.city,
                country = country_ids and country_ids[0] or False,
                bic = info.bic[:8],
            ))
        else:
            bank_id = False

    country_id = country_ids and country_ids[0] or False
    if not online or not bank_id:
        bank_id = bank_obj.create(cursor, uid, dict(
            code = info.code,
            name = _('Unknown Bank'),
            country = country_id,
            bic = bic,
        ))
    return bank_id, country_id

def create_bank_account(pool, cursor, uid, partner_id,
                        account_number, holder_name, log
                        ):
    '''
    Create a matching bank account with this holder for this partner.
    '''
    values = struct(
        partner_id = partner_id,
        owner_name = holder_name,
    )
    bankcode = None
    bic = None

    # Are we dealing with IBAN?
    iban = sepa.IBAN(account_number)
    if iban.valid:
        values.state = 'iban'
        values.acc_number = iban.BBAN
        bankcode = iban.bankcode + iban.countrycode
    else:
        # No, try to convert to IBAN
        country = pool.get('res.partner').browse(
            cursor, uid, partner_id).country_id
        values.state = 'bank'
        values.acc_number = account_number
        if country.code in sepa.IBAN.countries:
            account_info = sepa.online.account_info(country.code,
                                                    values.acc_number
                                                   )
            if account_info:
                values.iban = iban = account_info.iban
                values.state = 'iban'
                bankcode = account_info.code
                bic = account_info.bic

    if bic:
        values.bank_id = get_or_create_bank(pool, cursor, uid, bic)

    elif bankcode:
        # Try to link bank
        bank_obj = pool.get('res.bank')
        bank_ids = bank_obj.search(cursor, uid, [
            ('code', 'ilike', bankcode)
        ])
        if bank_ids:
            # Check BIC on existing banks
            values.bank_id = bank_ids[0]
            bank = bank_obj.browse(cursor, uid, values.bank_id)
            if not bank.bic:
                bank_obj.write(cursor, uid, values.bank_id, dict(bic=bic))
        else:
            # New bank - create
            values.bank_id = bank_obj.create(cursor, uid, dict(
                code = account_info.code,
                # Only the first eight positions of BIC are used for bank
                # transfers, so ditch the rest.
                bic = account_info.bic[:8],
                name = account_info.bank,
                country_id = country.id,
            ))

    # Create bank account and return
    return pool.get('res.partner.bank').create(cursor, uid, values)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: