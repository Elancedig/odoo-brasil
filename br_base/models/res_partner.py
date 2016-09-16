# -*- coding: utf-8 -*-
# © 2009 Gabriel C. Stabel
# © 2009 Renato Lima (Akretion)
# © 2012 Raphaël Valyi (Akretion)
# © 2015  Michell Stuttgart (KMEE)
# © 2016 Danimar Ribeiro <danimaribeiro@gmail.com>, Trustcode
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).


import re
import base64

from odoo import models, fields, api, _
from odoo.addons.br_base.tools import fiscal
from odoo.exceptions import UserError
from pytrustnfe.nfe import consulta_cadastro
from pytrustnfe.certificado import Certificado


class ResPartner(models.Model):
    _inherit = 'res.partner'

    cnpj_cpf = fields.Char('CNPJ/CPF', size=18, copy=False)
    inscr_est = fields.Char('Inscr. Estadual', size=16, copy=False)
    rg_fisica = fields.Char('RG', size=16, copy=False)
    inscr_mun = fields.Char('Inscr. Municipal', size=18)
    suframa = fields.Char('Suframa', size=18)
    legal_name = fields.Char(
        u'Razão Social', size=60, help="nome utilizado em documentos fiscais")
    city_id = fields.Many2one(
        'res.state.city', u'Município',
        domain="[('state_id','=',state_id)]")
    district = fields.Char('Bairro', size=32)
    number = fields.Char(u'Número', size=10)

    _sql_constraints = [
        ('res_partner_cnpj_cpf_uniq', 'unique (cnpj_cpf)',
         u'Já existe um parceiro cadastrado com este CPF/CNPJ!')
    ]

    @api.v8
    def _display_address(self, without_company=False):
        address = self

        if address.country_id and address.country_id.code != 'BR':
            # this ensure other localizations could do what they want
            return super(ResPartner, self)._display_address(
                without_company=False)
        else:
            address_format = (
                address.country_id and address.country_id.address_format or
                "%(street)s\n%(street2)s\n%(city)s %(state_code)s"
                "%(zip)s\n%(country_name)s")
            args = {
                'state_code': address.state_id and address.state_id.code or '',
                'state_name': address.state_id and address.state_id.name or '',
                'country_code': address.country_id and
                address.country_id.code or '',
                'country_name': address.country_id and
                address.country_id.name or '',
                'company_name': address.parent_id and
                address.parent_id.name or '',
                'city_name': address.city_id and
                address.city_id.name or '',
            }
            address_field = ['title', 'street', 'street2', 'zip', 'city',
                             'number', 'district']
            for field in address_field:
                args[field] = getattr(address, field) or ''
            if without_company:
                args['company_name'] = ''
            elif address.parent_id:
                address_format = '%(company_name)s\n' + address_format
            return address_format % args

    @api.one
    @api.constrains('cnpj_cpf', 'country_id')
    def _check_cnpj_cpf(self):
        country_code = self.country_id.code or ''
        if self.cnpj_cpf and country_code.upper() == 'BR':
            if self.is_company:
                if not fiscal.validate_cnpj(self.cnpj_cpf):
                    raise UserError(_(u'CNPJ inválido!'))
            elif not fiscal.validate_cpf(self.cnpj_cpf):
                raise UserError(_(u'CPF inválido!'))
        return True

    def _validate_ie_param(self, uf, inscr_est):
        try:
            mod = __import__(
                'odoo.addons.br_base.tools.fiscal', globals(),
                locals(), 'fiscal')

            validate = getattr(mod, 'validate_ie_%s' % uf)
            if not validate(inscr_est):
                return False
        except AttributeError:
            if not fiscal.validate_ie_param(uf, inscr_est):
                return False
        return True

    @api.one
    @api.constrains('inscr_est')
    def _check_ie(self):
        """Checks if company register number in field insc_est is valid,
        this method call others methods because this validation is State wise

        :Return: True or False."""
        if (not self.inscr_est or self.inscr_est == 'ISENTO' or
           not self.is_company):
            return True
        uf = (self.state_id and
              self.state_id.code.lower() or '')
        res = self._validate_ie_param(uf, self.inscr_est)
        if not res:
            raise UserError(_(u'Inscrição Estadual inválida!'))
        return True

    @api.one
    @api.constrains('inscr_est')
    def _check_ie_duplicated(self):
        """ Check if the field inscr_est has duplicated value
        """
        if (not self.inscr_est or self.inscr_est == 'ISENTO'):
            return True
        partner_ids = self.search(
            ['&', ('inscr_est', '=', self.inscr_est), ('id', '!=', self.id)])

        if len(partner_ids) > 0:
            raise UserError(_(u'Já existe um parceiro cadastrado com'
                            u'esta Inscrição Estadual/RG!'))
        return True

    @api.onchange('cnpj_cpf')
    def _onchange_cnpj_cpf(self):
        country_code = self.country_id.code or ''
        if self.cnpj_cpf and country_code.upper() == 'BR':
            val = re.sub('[^0-9]', '', self.cnpj_cpf)
            if len(val) == 14:
                cnpj_cpf = "%s.%s.%s/%s-%s"\
                    % (val[0:2], val[2:5], val[5:8], val[8:12], val[12:14])
                self.cnpj_cpf = cnpj_cpf
            elif not self.is_company and len(val) == 11:
                cnpj_cpf = "%s.%s.%s-%s"\
                    % (val[0:3], val[3:6], val[6:9], val[9:11])
                self.cnpj_cpf = cnpj_cpf
            else:
                raise UserError(_(u'Verifique o CNPJ/CPF'))

    @api.onchange('city_id')
    def _onchange_city_id(self):
        """ Ao alterar o campo city_id copia o nome
        do município para o campo city que é o campo nativo do módulo base
        para manter a compatibilidade entre os demais módulos que usam o
        campo city.
        """
        if self.city_id:
            self.city = self.city_id.name

    @api.onchange('zip')
    def onchange_mask_zip(self):
        if self.zip:
            val = re.sub('[^0-9]', '', self.zip)
            if len(val) == 8:
                zip = "%s-%s" % (val[0:5], val[5:8])
                self.zip = zip

    @api.model
    def _address_fields(self):
        """ Returns the list of address fields that are synced from the parent
        when the `use_parent_address` flag is set.
        Extenção para os novos campos do endereço """
        address_fields = super(ResPartner, self)._address_fields()
        return list(address_fields + ['city_id', 'number', 'district'])

    @api.one
    def action_check_sefaz(self):
        if self.cnpj_cpf and self.state_id:
            company = self.env.user.company_id
            if not company.nfe_a1_file and not company.nfe_a1_password:
                raise UserError(u'Configure o certificado e senha na empresa')
            cert = company.with_context({'bin_size': False}).nfe_a1_file
            cert_pfx = base64.decodestring(cert)
            certificado = Certificado(cert_pfx, company.nfe_a1_password)
            cnpj = re.sub('[^0-9]', '', self.cnpj_cpf)
            obj = {'cnpj': cnpj, 'estado': self.state_id.code}
            resposta = consulta_cadastro(certificado, obj=obj, ambiente=1,
                                         estado=self.state_id.ibge_code)

            obj = resposta['object']
            if "Body" in dir(obj) and \
               "consultaCadastro2Result" in dir(obj.Body):
                info = obj.Body.consultaCadastro2Result.retConsCad.infCons
                if info.cStat == 111 or info.cStat == 112:
                    if not self.inscr_est:
                        self.inscr_est = info.infCad.IE
                    if not self.cnpj_cpf:
                        self.cnpj_cpf = info.infCad.IE

                    def get_value(obj, prop):
                        if prop not in dir(obj):
                            return None
                        return getattr(obj, prop)

                    #TODO Buscar o municipio
                    self.legal_name = get_value(info.infCad, 'xNome')
                    self.zip = get_value(info.infCad.ender, 'CEP')
                    self.street = get_value(info.infCad.ender, 'xLgr')
                    self.number = get_value(info.infCad.ender, 'nro')
                    self.street2 = get_value(info.infCad.ender, 'xCpl')
                    self.district = get_value(info.infCad.ender, 'xBairro')
                    self.zip = get_value(info.infCad.ender, 'cMun')
                else:
                    msg = "%s - %s" % (info.cStat, info.xMotivo)
                    raise UserError(msg)
        else:
            raise UserError(u'Preencha o estado e o CNPJ para pesquisar')


class ResBank(models.Model):
    _inherit = 'res.bank'

    number = fields.Char(u'Número', size=10)
    street2 = fields.Char('Street2', size=128)
    district = fields.Char('Bairro', size=32)
    city_id = fields.Many2one(comodel_name='res.state.city',
                              string='Municipio',
                              domain="[('state_id','=',state_id)]")

    @api.onchange('city_id')
    def onchange_city_id(self):
        """ Ao alterar o campo city_id copia o nome
        do município para o campo city que é o campo nativo do módulo base
        para manter a compatibilidade entre os demais módulos que usam o
        campo city.
        """
        if self.city_id:
            self.city = self.city_id.name


class ResPartnerBank(models.Model):
    """ Adiciona campos necessários para o cadastramentos de contas
    bancárias no Brasil."""
    _inherit = 'res.partner.bank'

    acc_number = fields.Char('Account Number', size=64, required=False)
    acc_number_dig = fields.Char(u'Digito Conta', size=8)
    bra_number = fields.Char(u'Agência', size=8)
    bra_number_dig = fields.Char(u'Dígito Agência', size=8)
