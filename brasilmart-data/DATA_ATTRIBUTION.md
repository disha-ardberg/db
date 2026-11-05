# Data attribution

The records in `demo/` are selected and transformed from **Brazilian E-Commerce Public Dataset by Olist**, available at https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce. The Kaggle data card lists its license as **CC BY-NC-SA 4.0**: https://creativecommons.org/licenses/by-nc-sa/4.0/.

Changes made here: selected 30 source-backed orders and their related customer, item, and payment rows; renamed and joined columns; added category translations, validation flags, derived payment aggregates, fixture expectations, and BrasilMart training configuration. The original Olist CSVs are not included in this folder. BrasilMart policies and synthetic Teams identity mappings are separate training content, not supplied by Olist.
