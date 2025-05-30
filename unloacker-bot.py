import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from PyPDF2 import PdfReader, PdfWriter
import tempfile
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States for conversation
WAITING_FOR_PASSWORD = 1

# Dictionary to store user's PDF file paths and filenames
user_files = {}

# Create a thread pool for PDF processing
thread_pool = ThreadPoolExecutor(max_workers=4)

def process_pdf(pdf_path, password):
    """Process PDF in a separate thread to avoid blocking."""
    try:
        # Read the PDF with the provided password
        reader = PdfReader(pdf_path)
        if reader.is_encrypted:
            reader.decrypt(password)

        # Create a new PDF without password
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)

        # Create a temporary file for the unlocked PDF
        unlocked_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        unlocked_pdf.close()

        # Save the unlocked PDF
        with open(unlocked_pdf.name, 'wb') as output_file:
            writer.write(output_file)

        return unlocked_pdf.name, None
    except Exception as e:
        return None, str(e)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        'Welcome to PDF Password Remover Bot! 👋\n\n'
        'Send me a password-protected PDF file, and I will help you remove its password.\n'
        'I will first try the default password "@cdinotes". If that doesn\'t work, I will ask you for the correct password.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        'How to use this bot:\n\n'
        '1. Send me a password-protected PDF file\n'
        '2. I will first try the default password "@cdinotes"\n'
        '3. If that doesn\'t work, I will ask you for the correct password\n'
        '4. Wait for the bot to process the file\n'
        '5. Receive the unlocked PDF file\n\n'
        'Commands:\n'
        '/start - Start the bot\n'
        '/help - Show this help message\n'
        '/cancel - Cancel the current operation'
    )

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the PDF file sent by the user."""
    if not update.message.document:
        await update.message.reply_text('Please send a PDF file.')
        return ConversationHandler.END

    if not update.message.document.file_name.lower().endswith('.pdf'):
        await update.message.reply_text('Please send a PDF file.')
        return ConversationHandler.END

    # Create a temporary file to store the PDF
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
    temp_file.close()

    # Download the PDF file
    file = await context.bot.get_file(update.message.document.file_id)
    await file.download_to_drive(temp_file.name)

    # Store the file path and original filename for this user
    user_files[update.effective_user.id] = {
        'path': temp_file.name,
        'filename': update.message.document.file_name
    }

    # Try the default password first
    processing_message = await update.message.reply_text('Trying default password "@cdinotes"...')
    
    loop = asyncio.get_event_loop()
    try:
        unlocked_pdf_path, error = await asyncio.wait_for(
            loop.run_in_executor(thread_pool, process_pdf, temp_file.name, '@cdinotes'),
            timeout=300
        )
        
        if error is None:
            # Default password worked
            original_filename = user_files[update.effective_user.id]['filename']
            
            # Remove @cdinotes from filename if it exists
            if '@cdinotes' in original_filename.lower():
                output_filename = original_filename.lower().replace('@cdinotes', '')
                if output_filename.endswith('.pdf.pdf'):
                    output_filename = output_filename[:-4]
            else:
                output_filename = original_filename

            # Send the unlocked PDF
            with open(unlocked_pdf_path, 'rb') as unlocked_file:
                await update.message.reply_document(
                    document=unlocked_file,
                    filename=output_filename
                )

            # Clean up
            os.remove(temp_file.name)
            os.remove(unlocked_pdf_path)
            del user_files[update.effective_user.id]
            
            await processing_message.edit_text('PDF has been successfully unlocked using default password! 🎉')
            return ConversationHandler.END
            
    except asyncio.TimeoutError:
        await processing_message.edit_text('Processing took too long. Please try again with a smaller PDF file.')
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error with default password: {str(e)}")

    # If we get here, the default password didn't work
    await processing_message.edit_text('Default password didn\'t work. Please send me the correct password for this PDF file.')
    return WAITING_FOR_PASSWORD

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the password sent by the user."""
    user_id = update.effective_user.id
    if user_id not in user_files:
        await update.message.reply_text('No PDF file found. Please send a PDF file first.')
        return ConversationHandler.END

    password = update.message.text
    pdf_path = user_files[user_id]['path']
    original_filename = user_files[user_id]['filename']

    try:
        # Send a processing message
        processing_message = await update.message.reply_text('Processing your PDF, please wait...')

        # Process PDF in a separate thread with timeout
        loop = asyncio.get_event_loop()
        try:
            unlocked_pdf_path, error = await asyncio.wait_for(
                loop.run_in_executor(thread_pool, process_pdf, pdf_path, password),
                timeout=300  # 5 minutes timeout
            )
        except asyncio.TimeoutError:
            await processing_message.edit_text('Processing took too long. Please try again with a smaller PDF file.')
            return ConversationHandler.END

        if error:
            await processing_message.edit_text(f'Error processing PDF: {error}')
            return ConversationHandler.END

        # Remove @cdinotes from filename if it exists
        if '@cdinotes' in original_filename.lower():
            output_filename = original_filename.lower().replace('@cdinotes', '')
            if output_filename.endswith('.pdf.pdf'):
                output_filename = output_filename[:-4]
        else:
            output_filename = original_filename

        # Send the unlocked PDF back to the user with modified filename
        with open(unlocked_pdf_path, 'rb') as unlocked_file:
            await update.message.reply_document(
                document=unlocked_file,
                filename=output_filename
            )

        # Clean up temporary files
        os.remove(pdf_path)
        os.remove(unlocked_pdf_path)
        del user_files[user_id]

        await processing_message.edit_text('PDF has been successfully unlocked! 🎉')
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        await update.message.reply_text('Sorry, there was an error processing your PDF. Please make sure the password is correct and try again.')
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation."""
    if update.effective_user.id in user_files:
        # Clean up the temporary file
        if os.path.exists(user_files[update.effective_user.id]['path']):
            os.remove(user_files[update.effective_user.id]['path'])
        del user_files[update.effective_user.id]
    
    await update.message.reply_text('Operation cancelled.')
    return ConversationHandler.END

def main():
    """Start the bot."""
    # Replace 'YOUR_BOT_TOKEN' with your actual bot token
    application = Application.builder().token('6387403614:AAG_4bwtbYk26Quumumlge2bAG-Qeh_69oY').build()

    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL, handle_pdf)],
        states={
            WAITING_FOR_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)

    # Start the Bot
    application.run_polling()

if __name__ == '__main__':
    main()
